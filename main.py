import requests
import time
from datetime import datetime
import concurrent.futures
import itertools
import logging

# ログ設定
logging.basicConfig(
    filename='dex_arbitrage.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(message)s'
)

# Jupiter APIのエンドポイント
QUOTE_API_URL = "https://quote-api.jup.ag/v6/quote"

# トークンのMintアドレス
TOKEN_MINTS = {
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
}

# スワップ量（単位：トークンの最小単位）
AMOUNT = 1_000_000  # 1,000 トークン

# サポートされているDEXラベル
SUPPORTED_DEXES = [
    "Raydium", "Orca V2"
]

# 実行間隔（秒）
EXECUTION_INTERVAL = 30  # 30秒

# アービトラージの閾値（例: 0.1%）
ARBITRAGE_THRESHOLD = 0.1  # 0.1%

def get_quote(input_mint, output_mint, dex, retries=3, backoff=1):
    """
    指定したDEXでJupiter APIから価格見積もりを取得する。
    リトライ機能付き。
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": AMOUNT,
        "slippageBps": 50,
        "dexes": dex  # 特定のDEXを指定
    }
    for attempt in range(retries):
        try:
            response = requests.get(QUOTE_API_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            logging.info(f"DEX: {dex}, Input: {input_mint}, Output: {output_mint}, Amount: {AMOUNT}, Response: {data}")
            return data
        except requests.RequestException as e:
            logging.error(f"DEX: {dex} でのリクエスト失敗（試行 {attempt + 1}/{retries}）：{e}")
            time.sleep(backoff * (2 ** attempt))
    return None

def log_results(content):
    """
    ログファイルに結果を記録する。
    """
    with open("dex_arbitrage_results.log", "a") as log_file:
        log_file.write(content + "\n")

def process_swap_direction(input_mint, output_mint):
    """
    指定したトークンペアの指定方向でスワップ見積もりを取得し、最良の価格を返す。
    価格は1トークンあたりに計算する。
    """
    best_buy_price = 0
    best_buy_dex = None

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(get_quote, input_mint, output_mint, dex): dex for dex in SUPPORTED_DEXES}
        for future in concurrent.futures.as_completed(futures):
            dex = futures[future]
            try:
                quote_data = future.result()
                if not quote_data or "routePlan" not in quote_data:
                    logging.info(f"DEX: {dex} からの見積もりに有効なデータがありません。")
                    continue
                for route in quote_data["routePlan"]:
                    swap_info = route["swapInfo"]
                    in_amount = int(swap_info["inAmount"]) / 10**6  # 入力トークンの量
                    out_amount = int(swap_info["outAmount"]) / 10**6 # 出力トークンの量

                    # 価格計算を正しく修正
                    price = out_amount / in_amount  # 単純に出力量/入力量

                    # サニティチェック：価格が市場の範囲内か確認
                    if price < 0 or price > 100:  # 例えば1トークンあたり100以下を想定
                        logging.warning(f"DEX: {dex} から取得した価格 {price} は不正です。無視します。")
                        continue

                    if price > best_buy_price:
                        best_buy_price = price
                        best_buy_dex = dex
            except Exception as e:
                logging.error(f"DEX: {dex}でエラーが発生しました: {e}")

    return best_buy_price, best_buy_dex

def is_pair_tradable(input_mint, output_mint):
    """
    指定したトークンペアが取引可能かどうかを確認する。
    """
    test_dex = SUPPORTED_DEXES[0]  # 任意のDEXでテスト
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": AMOUNT,
        "slippageBps": 50,
        "dexes": test_dex
    }
    try:
        response = requests.get(QUOTE_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            logging.info(f"ペア {input_mint} ↔ {output_mint} にエラーが含まれています。取引不可能です。")
            return False
        if "routePlan" in data and len(data["routePlan"]) > 0:
            return True
        logging.info(f"ペア {input_mint} ↔ {output_mint} に有効なルートがありません。取引不可能です。")
        return False
    except requests.RequestException as e:
        logging.error(f"ペア {input_mint} ↔ {output_mint} の取引可能性確認中にエラーが発生しました：{e}")
        return False

def main():
    """
    メイン処理：すべてのトークンペアで価格見積もりを取得し、アービトラージの機会を特定。
    スワップ量を固定して実行。
    """
    while True:
        print("価格見積もりを取得中...")
        log_content = f"--- 実行日時: {datetime.now()} ---\n"

        # スワップ量の固定
        current_amount = AMOUNT / 1_000_000  # トークン数（例: 1,000）
        print(f"スワップ量: {current_amount} トークン")

        tokens = list(TOKEN_MINTS.keys())
        token_pairs = list(itertools.combinations(tokens, 2))

        tradable_pairs = []
        print("取引可能なペアを確認中...")
        for pair in token_pairs:
            token_a, token_b = pair
            mint_a = TOKEN_MINTS[token_a]
            mint_b = TOKEN_MINTS[token_b]
            if is_pair_tradable(mint_a, mint_b) and is_pair_tradable(mint_b, mint_a):
                tradable_pairs.append(pair)
            else:
                print(f"ペア {token_a} ↔ {token_b} は取引不可能です。スキップします。")

        if not tradable_pairs:
            log_content += f"スワップ量: {current_amount} トークン - 取引可能なペアが見つかりませんでした。\n"
            print(log_content)
            log_results(log_content)
            time.sleep(EXECUTION_INTERVAL)
            continue

        arbitrage_opportunities = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_pair = {}
            for pair in tradable_pairs:
                token_a, token_b = pair
                mint_a = TOKEN_MINTS[token_a]
                mint_b = TOKEN_MINTS[token_b]
                future_buy = executor.submit(process_swap_direction, mint_a, mint_b)
                future_sell = executor.submit(process_swap_direction, mint_b, mint_a)
                future_to_pair[future_buy] = (pair, "A->B")
                future_to_pair[future_sell] = (pair, "B->A")

            results = {}
            for future in concurrent.futures.as_completed(future_to_pair):
                pair, direction = future_to_pair[future]
                try:
                    price, dex = future.result()
                    if pair not in results:
                        results[pair] = {}
                    results[pair][direction] = (price, dex)
                except Exception as e:
                    print(f"ペア: {pair} の {direction} 方向でエラーが発生しました: {e}")

        for pair, directions in results.items():
            token_a, token_b = pair
            if "A->B" in directions and "B->A" in directions:
                buy_price, buy_dex = directions["A->B"]
                sell_price, sell_dex = directions["B->A"]

                if buy_price == 0 or sell_price == 0:
                    continue

                # 理論的な売却レート: 1 / buy_price
                theoretical_sell_price = 1 / buy_price
                price_diff = sell_price - theoretical_sell_price
                price_diff_percentage = (price_diff / theoretical_sell_price) * 100

                if price_diff_percentage >= ARBITRAGE_THRESHOLD:
                    opportunity = (
                        f"\nスワップ量: {current_amount} トークン\n"
                        f"トークンペア: {token_a} ↔ {token_b}\n"
                        f"  買い: {token_a} -> {token_b} at {buy_price:.6f} {token_b} per {token_a} (DEX: {buy_dex})\n"
                        f"  売り: {token_b} -> {token_a} at {sell_price:.6f} {token_a} per {token_b} (DEX: {sell_dex})\n"
                        f"  価格差: {price_diff:.6f} {token_a} per {token_b} ({price_diff_percentage:.4f}%)\n"
                        f"  アービトラージの機会が検出されました\n"
                    )
                    arbitrage_opportunities.append(opportunity)
                else:
                    log_content += (
                        f"\nスワップ量: {current_amount} トークン\n"
                        f"トークンペア: {token_a} ↔ {token_b}\n"
                        f"  買い: {token_a} -> {token_b} at {buy_price:.6f} {token_b} per {token_a} (DEX: {buy_dex})\n"
                        f"  売り: {token_b} -> {token_a} at {sell_price:.6f} {token_a} per {token_b} (DEX: {sell_dex})\n"
                        f"  価格差: {price_diff:.6f} {token_a} per {token_b} ({price_diff_percentage:.4f}%)\n"
                        f"  アービトラージの機会は検出されませんでした\n"
                    )

        if arbitrage_opportunities:
            for opp in arbitrage_opportunities:
                log_content += opp
        else:
            log_content += "\nアービトラージの機会は検出されませんでした。\n"

        print(log_content)
        log_results(log_content)

        # 指定間隔待機
        time.sleep(EXECUTION_INTERVAL)

if __name__ == "__main__":
    main()
