"""Build a Pages artifact from complete, dated NSE close reports."""
import argparse
import csv
import datetime as dt
import io
import json
import math
from pathlib import Path
from statistics import mean, pstdev
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WEEK = dt.timedelta(days=7)
DAY = dt.timedelta(days=1)
ALIASES = {
    "Nifty Pvt Bank": "Nifty Private Bank", "Nifty Fin Services": "Nifty Financial Services",
    "Nifty Healthcare": "Nifty Healthcare Index", "Nifty Cons Durables": "Nifty Consumer Durables",
    "Nifty Infra": "Nifty Infrastructure", "Nifty Consumption": "Nifty India Consumption",
}


def download(url):
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--location", "--retry", "2", "--max-time", "25",
         "--write-out", "\n%{http_code}", url], check=True, capture_output=True)
    body, code = result.stdout.rsplit(b"\n", 1)
    if code == b"404":
        return None
    if code != b"200":
        raise ValueError(f"HTTP {code.decode()} for {url}")
    return body.decode("utf-8-sig")


def weekly_close(sunday, names, holidays, through=None):
    for offset in range(7):
        date = sunday - offset * DAY
        if through and date > through:
            continue
        url = f"https://archives.nseindia.com/content/indices/ind_close_all_{date:%d%m%Y}.csv"
        body = download(url)
        if body is None:
            if date == dt.date(2026, 11, 8) or (date.weekday() < 5 and date not in holidays):
                raise ValueError(f"Missing report for expected trading date {date}")
            continue
        values = {r["Index Name"].strip().lower(): float(r["Closing Index Value"])
                  for r in csv.DictReader(io.StringIO(body))
                  if r["Index Date"] == date.strftime("%d-%m-%Y") and r["Closing Index Value"].strip()}
        selected = {n: values[ALIASES.get(n, n).lower()] for n in names}
        return {"date": date.isoformat(), "closes": selected}
    raise ValueError(f"No trading session for week ending {sunday}")


def calculate(rows, names):
    readings = {}
    for name in names:
        rs = [100 * r["closes"][name] / r["closes"]["Nifty 50"] for r in rows]

        def ratio(i):
            return 100 * rs[i] / mean(rs[i-13:i+1])

        tail, slow = [], []
        for i in range(len(rs)-5, len(rs)):
            window = rs[i-13:i+1]
            spread = pstdev(window) or 1
            x = 100 + 1.5 * (rs[i] - mean(window)) / spread
            y = 100 + 2.7 * (rs[i] - rs[i-1]) / spread
            tail.append([max(96, min(104, v)) for v in (x, y)])
            slow.append([ratio(i), 100 * ratio(i) / ratio(i-10)])
        readings[name] = {"tail": tail, "slow": slow}
    return readings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-ending", type=dt.date.fromisoformat, help="Sunday identifying the trading week (YYYY-MM-DD)")
    parser.add_argument("--output", type=Path, default=ROOT / "public")
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    through = now.date() if now.hour >= 19 else now.date() - DAY
    friday = through - (through.weekday() - 4) % 7 * DAY
    latest = friday + 2 * DAY
    end = args.week_ending or latest
    if end.weekday() != 6 or end > latest:
        raise ValueError("Choose a week whose Friday reporting cutoff has passed.")
    seed = json.loads((ROOT/"data/baseline.json").read_text())
    names = list(seed[-1]["closes"])
    if len(names) != 25 or "Nifty 50" not in names:
        raise ValueError("Expected the benchmark and 24 sectors")
    cached = {}
    for row in seed:
        d = dt.date.fromisoformat(row["date"])
        cached[d + (6-d.weekday())*DAY] = row
    holidays = {dt.date.fromisoformat(x) for x in json.loads((ROOT/"data/holidays.json").read_text())}
    rows = []
    for i in reversed(range(28)):
        sunday = end - i*WEEK
        row = cached.get(sunday) or weekly_close(sunday, names, holidays, through)
        if any(not math.isfinite(row["closes"][n]) or row["closes"][n] <= 0 for n in names):
            raise ValueError(f"Invalid close in {row['date']}")
        rows.append(row)
    readings = calculate(rows, [n for n in names if n != "Nifty 50"])
    dates = [dt.date.fromisoformat(r["date"]).strftime("%d %b %Y") for r in rows[-5:]]
    template = (ROOT/"index.html").read_text(encoding="utf8")
    insert = "    let focusedSector = null;"
    if template.count(insert) != 1:
        raise ValueError("Unexpected dashboard template")
    script = ("    const weeklyReadings = " + json.dumps(readings, allow_nan=False) + ";\n"
              "    datasets.IN.forEach(s => Object.assign(s, weeklyReadings[s.name]));\n"
              "    closeDates.splice(0, closeDates.length, ..." + json.dumps(dates) + ");\n")
    html = template.replace(insert, script + insert).replace("30 Oct 2026", dates[-1])
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "index.html"
    temp = target.with_suffix(".tmp")
    temp.write_text(html, encoding="utf8")
    temp.replace(target)
    print(f"Built 24 readings through {dates[-1]}")


if __name__ == "__main__":
    main()


