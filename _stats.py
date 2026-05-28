import sqlite3
c = sqlite3.connect("C:/Users/BrianChan/Documents/crypto_trading_bot/data/trading_bot.db")

def agg(rows):
    cl = [r for r in rows if r[0] is not None]
    wins = [r for r in cl if r[0] > 0]
    losses = [r for r in cl if r[0] <= 0]
    n = len(cl)
    if n == 0:
        return "no closed trades"
    wr = 100.0 * len(wins) / n
    avg_w = sum(r[1] for r in wins) / len(wins) if wins else 0
    avg_l = sum(r[1] for r in losses) / len(losses) if losses else 0
    gross_w = sum(r[0] for r in wins)
    gross_l = abs(sum(r[0] for r in losses))
    pf = (gross_w / gross_l) if gross_l else float("inf")
    expectancy = sum(r[0] for r in cl) / n
    return (f"n={n} winrate={wr:.1f}% avgWin%={avg_w:.2f} avgLoss%={avg_l:.2f} "
            f"profitFactor={pf:.2f} expectancy=${expectancy:.2f} sumPnl=${sum(r[0] for r in cl):.2f}")

allrows = c.execute("SELECT asset_type, realized_pnl, realized_pnl_pct FROM trades WHERE status='CLOSED'").fetchall()
print("ALL   ", agg([(r[1], r[2]) for r in allrows]))
print("CRYPTO", agg([(r[1], r[2]) for r in allrows if r[0] == 'crypto']))
print("STOCK ", agg([(r[1], r[2]) for r in allrows if r[0] == 'stock']))

print("--- holding period hours (closed) ---")
for r in c.execute("SELECT asset_type, AVG((julianday(closed_at)-julianday(opened_at))*24) FROM trades WHERE status='CLOSED' AND closed_at IS NOT NULL GROUP BY asset_type"):
    print(r[0], round(r[1], 2) if r[1] else None)

print("--- open positions ---")
for r in c.execute("SELECT symbol, asset_type, ROUND(entry_price,2), ROUND(position_size_usd,2) FROM trades WHERE status='OPEN'"):
    print(r)

print("--- signal actionability ---")
for r in c.execute("SELECT asset_type, signal_type, COUNT(*) FROM signals GROUP BY asset_type, signal_type"):
    print(r)
