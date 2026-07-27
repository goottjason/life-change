# 거래 빈도 확대 검증 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RSI(2)/ATR% 문턱 완화·1분봉 추가·30분/1시간봉 병행·미검증 종목 편입 네 가지 축을 동일한 체결·비용 모델로 백테스트해, "빈도를 늘리면서 일일 기댓값을 지키는 구성"이 존재하는지 판정한다.

**Architecture:** 기존 리서치 스크립트 패턴(`lab_*.py` + `fastsim` 벡터화 코어)을 그대로 따른다. 타임프레임별 파라미터 환산·리샘플·추세필터·판정을 새 공통 모듈 `lab_tfcore.py`로 뽑고, 축별 스크립트가 그것을 호출한다. 데이터 수집(`data_cache.py`, `spread_check.py`)은 청크·이어받기와 낙관/보수 두 벌 스프레드를 지원하도록 확장한다.

**Tech Stack:** Python 3.9 (`.venv`), pandas, numpy, pyupbit, pytest. 실행은 항상 `cd upbit-rbi-bot && .venv/bin/python ...`.

## Global Constraints

이 절의 값은 모든 태스크의 요구사항에 포함된다. 하나라도 어기면 결과는 폐기한다.

- **체결 모델 변경 금지**: `ENTRY_DELAY = 1`(신호 다음 봉 **시가** 진입), `exit_mode="intrabar"`(같은 봉에서 손절·익절 동시 도달 시 **손절 우선**). 상수는 `backtesting/research/lab_screen.py`에서 import한다. 새로 정의하지 않는다.
- **비용 모델**: 왕복 수수료 `C.FEE_ROUNDTRIP = 0.001` + 종목별 실측 스프레드를 `simulate_arrays(slippage=...)`로 전달.
- **선택/판정 분리**: `SELECTION_START = pd.Timestamp("2025-12-29")`(`lab_final.py`에서 import). 이후 구간에서만 파라미터를 고른다. 그 이전 17개월은 홀드아웃.
- **홀드아웃 조회 예산 총 4회**: 축 2(`lab_frontier.py`) 1회 · 축 3(`lab_tfscan.py`) 1회 · 축 4(`lab_universe.py`) 1회 · 최종(`lab_decide.py`) 1회. 각 스크립트는 홀드아웃 구간 판정을 **최종 후보 1개에만** 적용하고, 출력 마지막 줄에 `홀드아웃 조회 1회` 를 찍는다.
- **사전 등록한 합격 하한 3개** (사후 변경 금지):
  - ① walk-forward OOS `exp > 0` AND `t > 2.0`(다중비교 보정 후 필요 t 이상)
  - ② 종목별 스프레드 대신 **`slippage = 0.0015` 고정**(수수료 0.1% + 0.15% = 왕복 0.25%)으로 다시 돌렸을 때도 `exp > 0`
  - ③ `portfolio.run()` 결과 `mdd <= 0.20`
- **목적함수**: `일일 기댓값 = exp × (trades / days)`. 거래당 exp가 현행(+0.259%)보다 낮아도 일일 기댓값이 높으면 후보로 인정한다. 단 하한 3개는 예외 없음.
- **헌장 §11은 배포 관문**(거래≥100·승률≥55%·PF≥1.5·MDD≤20%). 연구 선택 기준과 별개이며, 최종 후보가 §11을 통과하지 못하면 그 사실을 명시 출력한다.
- **API 호출 스크립트는 단독 실행**: `spread_check.py`, `data_cache.py`. 동시에 두 개를 돌리면 레이트리밋으로 0봉 실패한다(실제 겪음).
- **파일 위치**: 리서치 스크립트는 `upbit-rbi-bot/backtesting/research/`, 테스트는 `upbit-rbi-bot/tests/`.
- 기존 `spreads.json` · `spreads_mid.json`은 **수정·삭제 금지**(과거 결과 재현성). 새 값은 새 파일에 쓴다.

## File Structure

| 파일 | 책임 |
|---|---|
| `backtesting/research/lab_tfcore.py` (신규) | 타임프레임 파라미터 환산·리샘플·상위TF 추세필터·신호·단일종목 시뮬·일일기댓값·하한판정. 축 2·3·4·최종이 전부 여기를 쓴다 |
| `backtesting/research/spread_check.py` (수정) | 낙관/보수 두 벌 스프레드 저장, 24시간 장기 샘플러 모드 추가 |
| `backtesting/research/data_cache.py` (수정) | 청크·이어받기 수집, 1분봉 모드, 캐시 충족 판정을 상장기간 기준으로 |
| `backtesting/research/lab_frontier.py` (신규) | 축 2 — RSI 진입선 × ATR 게이트 2D frontier |
| `backtesting/research/lab_tfscan.py` (신규) | 축 3 — 1·5·15·30·60분 타임프레임 스캔 |
| `backtesting/research/lab_universe.py` (수정) | 축 4 — 미검증 종목 + 낙관/보수 병기 |
| `backtesting/research/lab_decide.py` (신규) | 최종 통합 — 포트폴리오 병합·하한 3개 판정·§11 병기 |
| `tests/test_tfcore.py` (신규) | `lab_tfcore` 단위 테스트(리샘플 일치·시간손절·lookahead·판정 경계) |
| `tests/test_data_cache_chunk.py` (신규) | 청크 수집기 이어받기·중복 없음 |
| `backtesting/research/README.md` (수정) | 결과표·실행법 추가 |

---

### Task 1: 스프레드 낙관/보수 두 벌 측정

**Files:**
- Modify: `backtesting/research/spread_check.py`
- Test: 없음(순수 네트워크 I/O 스크립트). 판정 로직은 Task 3의 `lab_tfcore`에서 테스트한다.

**Interfaces:**
- Consumes: `data_cache.MARKETS_LAB`, `data_cache.MARKETS_MID`, `data_cache.DATA_DIR`
- Produces:
  - `sample_raw(markets: list[str], n: int, interval: float) -> dict[str, list[float]]` — 종목별 원시 샘플
  - `summarize(raw: dict[str, list[float]]) -> tuple[dict[str, float], dict[str, float]]` — `(낙관=중앙값, 보수)`
  - 파일 `data/spreads_opt.json`, `data/spreads_cons.json`

- [ ] **Step 1: 원시 샘플을 반환하는 함수로 분리**

`sample()`은 중앙값만 돌려주므로 분위수를 계산할 수 없다. 원시값을 반환하는 함수를 추가하고 기존 `sample()`은 그것을 감싸도록 바꾼다(기존 호출부 호환 유지).

```python
def sample_raw(markets: list[str], n: int = 8, interval: float = 15.0) -> dict[str, list[float]]:
    """스프레드 원시 샘플. 분위수를 계산하려면 중앙값이 아니라 표본 전체가 필요하다."""
    acc: dict[str, list[float]] = {m: [] for m in markets}
    for i in range(n):
        try:
            books = pyupbit.get_orderbook(markets)
            if isinstance(books, dict):
                books = [books]
            for b in books:
                u = b["orderbook_units"][0]
                bid, ask = float(u["bid_price"]), float(u["ask_price"])
                acc[b["market"]].append((ask - bid) / ((ask + bid) / 2))
        except Exception as e:
            print(f"  샘플 {i+1} 실패: {e}")
        if i < n - 1:
            time.sleep(interval)
    return {m: v for m, v in acc.items() if v}


def sample(markets: list[str], n: int = 8, interval: float = 15.0) -> dict[str, float]:
    """스프레드 중앙값(기존 호출부 호환)."""
    import statistics
    return {m: statistics.median(v) for m, v in sample_raw(markets, n, interval).items()}
```

- [ ] **Step 2: 낙관/보수 요약 함수 추가**

보수값의 정의를 코드에 명시한다. 2분 창의 상위 분위는 중앙값과 거의 같으므로, ×2 스트레스를 **가정으로** 넣고 실측 p75와 큰 쪽을 쓴다.

```python
NIGHT_WIDENING_ASSUMPTION = 2.0   # 백로그 실측(ATOM 0.049%→0.197%, ORCA 0.058%→0.231%) 기반 가정


def summarize(raw: dict[str, list[float]]) -> tuple[dict[str, float], dict[str, float]]:
    """(낙관, 보수) 두 벌.

    낙관 = 중앙값. 보수 = max(중앙값 × 2, 실측 p75).
    ×2 는 **측정값이 아니라 가정**이다 — 짧은 샘플 창(2분)은 심야 확대를 담지 못한다.
    24시간 샘플러(--watch)로 실측 분포를 얻으면 그 p75로 대체한다.
    """
    import statistics
    opt, cons = {}, {}
    for m, v in raw.items():
        s = sorted(v)
        med = statistics.median(s)
        p75 = s[min(len(s) - 1, int(len(s) * 0.75))]
        opt[m] = med
        cons[m] = max(med * NIGHT_WIDENING_ASSUMPTION, p75)
    return opt, cons
```

- [ ] **Step 3: `--sample2` 모드 추가 (두 벌 저장)**

`main()`의 `--sample` 분기 아래에 새 분기를 넣는다. 기존 `--sample` 동작은 건드리지 않는다.

```python
    if "--sample2" in sys.argv:
        import json
        n = 20
        print(f"스프레드 {n}회 샘플링 중(약 {n * 15 // 60}분) — 단독 실행할 것…")
        raw = sample_raw(markets, n=n, interval=15.0)
        opt, cons = summarize(raw)
        (data_cache.DATA_DIR / "spreads_opt.json").write_text(json.dumps(opt, indent=2))
        (data_cache.DATA_DIR / "spreads_cons.json").write_text(json.dumps(cons, indent=2))
        print(f"\n{'종목':8s} {'낙관(중앙)':>10s} {'보수':>10s} {'왕복비용(보수)':>14s} {'≤0.1%':>7s}")
        for m in sorted(opt, key=lambda k: opt[k]):
            print(f"{m.replace('KRW-',''):8s} {opt[m]:10.3%} {cons[m]:10.3%} "
                  f"{C.FEE_ROUNDTRIP + cons[m]:14.3%} {'✅' if opt[m] <= 0.001 else '❌'}")
        print("\n⚠️ 보수값의 ×2 는 가정이다(측정 아님). --watch 로 24시간 실측 후 대체할 것.")
        print("→ data/spreads_opt.json, data/spreads_cons.json 저장")
        return
```

`C`는 이 파일에 아직 import되어 있지 않다. 상단 import 절에 `from config import charter as C` 를 추가한다.

- [ ] **Step 4: `--watch` 24시간 샘플러 추가**

```python
    if "--watch" in sys.argv:
        import json
        n, interval = 144, 600.0      # 10분 간격 24시간
        print(f"24시간 스프레드 관측 시작 (10분 간격 {n}회) — 결과는 종료 시 저장")
        raw = sample_raw(markets, n=n, interval=interval)
        out = data_cache.DATA_DIR / "spreads_watch.json"
        out.write_text(json.dumps(raw, indent=2))
        opt, cons = summarize(raw)
        print(f"\n{'종목':8s} {'중앙':>9s} {'p75':>9s} {'최대':>9s}")
        for m in sorted(raw):
            s = sorted(raw[m])
            print(f"{m.replace('KRW-',''):8s} {s[len(s)//2]:9.3%} {cons[m]:9.3%} {s[-1]:9.3%}")
        print(f"→ {out} (원시 샘플 전량 · 시간대별 분석 가능)")
        return
```

- [ ] **Step 5: 문법·import 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -c "from backtesting.research import spread_check; print(spread_check.summarize({'KRW-X':[0.001,0.002,0.003,0.004]}))"`
Expected: `({'KRW-X': 0.0025}, {'KRW-X': 0.005})` 형태로 출력(중앙 0.0025, 보수 = max(0.005, p75=0.004) = 0.005)

- [ ] **Step 6: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/spread_check.py
git commit -m "feat(research): 스프레드 낙관/보수 두 벌 측정 + 24시간 관측 모드"
```

---

### Task 2: 1분봉 청크 수집기

**Files:**
- Modify: `backtesting/research/data_cache.py`
- Test: `tests/test_data_cache_chunk.py`

**Interfaces:**
- Consumes: `pyupbit.get_ohlcv(ticker, interval, count, to, period)`
- Produces:
  - `fetch_chunked(market: str, interval: str, count: int, chunk: int = 50_000) -> pd.DataFrame | None`
  - `merge_cache(existing: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame` — 중복 제거·시간순 정렬
  - `MARKETS_1M: list[str]`, `SPECS_1M: list[tuple[str, int]]`, 모드 문자열 `"min1"`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""청크 수집기 테스트 — 중단 후 이어받기가 중복·구멍 없이 동작해야 한다."""
from __future__ import annotations

import pandas as pd
import pytest

from backtesting.research import data_cache


def _bars(start: str, n: int, freq: str = "1min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq)
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                        index=idx)


def test_merge_cache_removes_duplicates_and_sorts():
    old = _bars("2026-01-01 01:00", 60)          # 01:00~01:59
    new = _bars("2026-01-01 00:30", 60)          # 00:30~01:29 (30분 겹침)
    merged = data_cache.merge_cache(old, new)
    assert merged.index.is_monotonic_increasing
    assert merged.index.is_unique
    assert len(merged) == 90                      # 00:30~01:59
    assert merged.index[0] == pd.Timestamp("2026-01-01 00:30")
    assert merged.index[-1] == pd.Timestamp("2026-01-01 01:59")


def test_merge_cache_accepts_empty_existing():
    new = _bars("2026-01-01 00:00", 10)
    merged = data_cache.merge_cache(None, new)
    assert len(merged) == 10


def test_fetch_chunked_pages_backwards_and_stops(monkeypatch):
    """count에 도달하면 멈추고, 각 호출의 to 가 직전 청크의 가장 오래된 시각이어야 한다."""
    calls = []

    def fake_get_ohlcv(ticker, interval=None, count=None, to=None, period=None):
        calls.append(to)
        end = pd.Timestamp(to) if to else pd.Timestamp("2026-01-01 00:00")
        idx = pd.date_range(end=end - pd.Timedelta(minutes=1), periods=count, freq="1min")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                            index=idx)

    monkeypatch.setattr(data_cache, "_get_ohlcv", fake_get_ohlcv)
    df = data_cache.fetch_chunked("KRW-BTC", "minute1", count=250, chunk=100)
    assert len(df) >= 250
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert calls[0] is None                       # 첫 호출은 최신부터
    assert len(calls) == 3                        # 100 + 100 + 50 → 3회
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_data_cache_chunk.py -v`
Expected: FAIL — `AttributeError: module 'backtesting.research.data_cache' has no attribute 'merge_cache'`

- [ ] **Step 3: 구현**

`data_cache.py`에 추가한다. `_get_ohlcv`를 얇은 래퍼로 두는 이유는 테스트에서 monkeypatch하기 위해서다(pyupbit를 직접 부르면 테스트가 네트워크를 탄다).

```python
MARKETS_1M = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-LINK", "KRW-BCH"]
SPECS_1M = [("minute1", 1_051_200)]      # 2년 (60 × 24 × 730)


def _get_ohlcv(ticker, interval=None, count=None, to=None, period=None):
    """pyupbit 래퍼 (테스트에서 대체하기 위해 분리)."""
    import pyupbit
    return pyupbit.get_ohlcv(ticker, interval=interval, count=count, to=to, period=period)


def merge_cache(existing: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """기존 캐시와 새 청크를 합친다. 중복 인덱스는 제거하고 시간순으로 정렬한다."""
    if existing is None or existing.empty:
        out = new
    else:
        out = pd.concat([existing, new])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def fetch_chunked(market: str, interval: str, count: int,
                  chunk: int = 50_000, resume: pd.DataFrame | None = None,
                  on_chunk=None) -> pd.DataFrame | None:
    """count봉을 chunk봉씩 과거로 거슬러 받는다.

    한 번의 호출로 100만 봉을 받으면 도중 실패 시 전량을 잃는다(1분봉 2년 = 약 5,300회 페이징).
    청크마다 on_chunk 콜백으로 저장하면 중단 후 이어받을 수 있다.
    resume: 이미 가진 캐시. 그 가장 오래된 시각부터 더 과거로 이어받는다.
    """
    acc = resume
    to = None if acc is None or acc.empty else acc.index[0].strftime("%Y-%m-%d %H:%M:%S")
    have = 0 if acc is None else len(acc)
    while have < count:
        want = min(chunk, count - have)
        try:
            df = _get_ohlcv(market, interval=interval, count=want, to=to, period=0.12)
        except Exception as e:
            print(f"  청크 실패({have}봉 확보 상태에서 중단): {e}", flush=True)
            break
        if df is None or df.empty:
            print(f"  더 이상 과거 데이터 없음({have}봉에서 종료)", flush=True)
            break
        df = df.rename(columns=str.lower)
        before = have
        acc = merge_cache(acc, df)
        have = len(acc)
        if have == before:                      # 새 봉이 하나도 안 늘면 상장 시작점
            print(f"  새 봉 없음 — 상장 시작점 도달({have}봉)", flush=True)
            break
        to = acc.index[0].strftime("%Y-%m-%d %H:%M:%S")
        if on_chunk is not None:
            on_chunk(acc)
        print(f"  {have:,}/{count:,}봉  {acc.index[0].date()}~{acc.index[-1].date()}", flush=True)
    return acc
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_data_cache_chunk.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: `main()`에 min1 모드 연결 + 캐시 충족 판정 수정**

`specs_for`와 `main()`을 수정한다. 캐시 충족 판정을 '요구 봉수의 90%'에서 '요구 봉수의 90% **또는** 더 이상 과거 데이터가 없음(직전 실행에서 상장 시작점 도달)'으로 바꾼다. 후자는 파일 옆에 `.complete` 마커로 기록한다 — 상장 기간이 짧은 종목(KAITO)을 매번 재수집하는 문제(백로그 우선순위 6)를 없앤다.

```python
def specs_for(mode: str):
    return {"lab": SPECS_LAB, "lab2": SPECS_LAB2, "mid": SPECS_MID,
            "min1": SPECS_1M}.get(mode, SPECS)


def marker_for(market: str, interval: str) -> Path:
    return DATA_DIR / f"{market}_{interval}.complete"
```

`main()`의 종목 선택과 캐시 판정 부분을 다음으로 교체한다.

```python
    markets = (MARKETS_MID if mode == "mid"
               else MARKETS_1M if mode == "min1"
               else MARKETS_LAB if mode.startswith("lab") else MARKETS)
    for interval, count in specs:
        for market in markets:
            p = path_for(market, interval)
            existing = load(market, interval) if p.exists() else None
            if existing is not None and not force:
                enough = len(existing) >= count * 0.9 or marker_for(market, interval).exists()
                if enough:
                    print(f"skip  {market:9s} {interval:9s} {len(existing):8,d}봉 "
                          f"{existing.index[0].date()}~{existing.index[-1].date()} (캐시)")
                    continue
                print(f"이어받기 {market:9s} {interval:9s} {len(existing):,}봉 → {count:,}봉",
                      flush=True)
            t0 = time.time()
            df = fetch_chunked(market, interval, count,
                               resume=None if force else existing,
                               on_chunk=lambda d, p=p: d.to_csv(p))
            if df is None or len(df) < 300:
                print(f"FAIL  {market:9s} {interval:9s} 데이터 부족({0 if df is None else len(df)}봉)")
                continue
            df.to_csv(p)
            if len(df) < count * 0.9:
                marker_for(market, interval).write_text(f"{len(df)}봉에서 상장 시작점 도달\n")
            print(f"save  {market:9s} {interval:9s} {len(df):8,d}봉 "
                  f"{df.index[0].date()}~{df.index[-1].date()} {time.time()-t0:6.1f}s", flush=True)
```

- [ ] **Step 6: 기존 캐시가 깨지지 않는지 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/data_cache.py 2>&1 | head -20`
Expected: 모든 줄이 `skip ... (캐시)` — 네트워크 호출이 일어나지 않아야 한다. 하나라도 `이어받기`가 나오면 판정 로직이 잘못됐다.

- [ ] **Step 7: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/data_cache.py upbit-rbi-bot/tests/test_data_cache_chunk.py
git commit -m "feat(research): 청크·이어받기 캔들 수집 + 1분봉 모드 + 캐시 충족 판정 수정"
```

---

### Task 3: 공통 코어 `lab_tfcore.py`

**Files:**
- Create: `backtesting/research/lab_tfcore.py`
- Test: `tests/test_tfcore.py`

**Interfaces:**
- Consumes: `lab_screen.ENTRY_DELAY`, `lab_screen.SLIPPAGE`, `lab_final.SELECTION_START`, `fastsim.{ExitCfg, Precomp, simulate_arrays, stats_chrono, Stats}`, `portfolio.{PortTrade, run}`, `ta.{rsi, atr, ema}`, `config.charter as C`
- Produces (다음 태스크가 이 이름·타입에 의존한다):
  - `TF_MINUTES: dict[str, int]`
  - `TfParams` dataclass: `.tf .minutes .gate .stop .time_stop_bars .warmup .trend_tf`
  - `params_for(tf: str, gate: float | None = None, stop: float | None = None) -> TfParams`
  - `resample(df: pd.DataFrame, tf: str) -> pd.DataFrame`
  - `trend_series(df: pd.DataFrame, trend_tf: str, ema_len: int = 200) -> np.ndarray`
  - `signals(df, th: float, gate: float, trend_tf: str) -> tuple[np.ndarray, np.ndarray]`
  - `run_market(df, p: TfParams, th: float, slip: float, *, start: int | None = None, end: int | None = None, market: str = "") -> tuple[list[tuple], list[PortTrade]]`
  - `daily_exp(s: Stats, days: int) -> float`
  - `Verdict` dataclass: `.passed .reasons`
  - `judge(oos: Stats, stress: Stats, port_mdd: float, n_compared: int) -> Verdict`
  - `required_t(n_compared: int) -> float`
  - `STRESS_SPREAD: float = 0.0015`
  - `fold_bounds(idx: pd.DatetimeIndex, warmup: int, is_days: int, oos_days: int) -> list[tuple[int, int, int]]`
  - `walk_forward(panel: dict, tf: str, grid: list[tuple[float, float]], spreads: dict, *, is_days: int = 139, oos_days: int = 69, min_is_trades: int = 50) -> tuple[Stats, list[PortTrade], list[tuple], int]` — `(OOS 합계 Stats, 포트폴리오 거래, 폴드별 선택 기록, OOS 일수)`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""lab_tfcore 단위 테스트.

여기서 막아야 하는 것:
 1. 시간손절이 타임프레임마다 8시간이 아니게 되는 것
 2. 리샘플이 업비트 실제 봉과 어긋나는 것 (다른 데이터로 검증한 셈이 된다)
 3. 상위 타임프레임 추세 필터가 미래를 보는 것 (lookahead — 허구의 엣지가 나온다)
 4. 합격 하한 3개가 조용히 느슨해지는 것
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.research import data_cache, lab_tfcore as tfcore
from backtesting.research.fastsim import Stats


def _synth(minutes: int, n: int, start: str = "2026-01-01 00:00") -> pd.DataFrame:
    """가짜 캔들 — 값은 단조 증가, 인덱스만 정확하면 되는 테스트용."""
    idx = pd.date_range(start, periods=n, freq=f"{minutes}min")
    close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999,
                         "close": close, "volume": 1.0}, index=idx)


# ── 1. 파라미터 환산 ────────────────────────────────────────────────
@pytest.mark.parametrize("tf", ["minute1", "minute5", "minute15", "minute30", "minute60"])
def test_time_stop_is_always_eight_hours(tf):
    p = tfcore.params_for(tf)
    assert p.minutes * p.time_stop_bars == 480


def test_gate_and_stop_match_live_charter_for_5m_and_15m():
    """현행 라이브 값(헌장 v2.2)을 그대로 재현해야 한다 — 여기가 어긋나면 다른 전략이다."""
    p5, p15 = tfcore.params_for("minute5"), tfcore.params_for("minute15")
    assert p5.gate == pytest.approx(0.006, abs=1e-4)
    assert p5.stop == pytest.approx(0.025)
    assert p5.time_stop_bars == 96
    assert p15.gate == pytest.approx(0.010, abs=5e-4)
    assert p15.stop == pytest.approx(0.030)
    assert p15.time_stop_bars == 32


def test_trend_tf_switches_to_4h_for_hourly_strategy():
    assert tfcore.params_for("minute5").trend_tf == "minute60"
    assert tfcore.params_for("minute30").trend_tf == "minute60"
    assert tfcore.params_for("minute60").trend_tf == "minute240"


def test_warmup_covers_ema200_of_trend_tf():
    for tf in ("minute1", "minute5", "minute15", "minute30", "minute60"):
        p = tfcore.params_for(tf)
        need = 200 * tfcore.TF_MINUTES[p.trend_tf] / p.minutes
        assert p.warmup >= need


def test_params_for_overrides():
    p = tfcore.params_for("minute5", gate=0.012, stop=0.04)
    assert (p.gate, p.stop) == (0.012, 0.04)


# ── 2. 리샘플이 업비트 실제 봉과 일치하는가 ──────────────────────────
def test_resample_5m_to_15m_matches_upbit_candles():
    df5 = data_cache.load("KRW-BTC", "minute5")
    df15 = data_cache.load("KRW-BTC", "minute15")
    if df5 is None or df15 is None:
        pytest.skip("캐시 없음 — data_cache.py lab2 실행 필요")
    got = tfcore.resample(df5, "minute15")
    common = got.index.intersection(df15.index)
    assert len(common) > 1000
    for col in ("open", "high", "low", "close"):
        a, b = got.loc[common, col], df15.loc[common, col]
        # 완성봉만 비교 (양쪽 끝의 부분봉 제외)
        assert np.allclose(a.iloc[1:-1], b.iloc[1:-1], rtol=1e-9)


def test_resample_60m_to_240m_matches_upbit_4h_boundaries():
    """업비트 4시간봉 경계는 01/05/09/13/17/21시(KST)다 — offset='1h'."""
    h = data_cache.load("KRW-BTC", "minute60")
    d4 = data_cache.load("KRW-BTC", "minute240")
    if h is None or d4 is None:
        pytest.skip("캐시 없음")
    got = tfcore.resample(h, "minute240")
    common = got.index.intersection(d4.index)
    assert len(common) > 500
    assert np.allclose(got.loc[common, "close"].iloc[1:-1],
                       d4.loc[common, "close"].iloc[1:-1], rtol=1e-9)


# ── 3. 추세 필터의 lookahead ────────────────────────────────────────
def test_trend_series_ignores_future_bars():
    """미래 봉을 조작해도 과거 값이 변하면 lookahead다."""
    base = _synth(5, 3600)                      # 5분봉 300시간
    a = tfcore.trend_series(base, "minute60", ema_len=5)
    mutated = base.copy()
    mutated.iloc[-30:, mutated.columns.get_loc("close")] *= 5
    b = tfcore.trend_series(mutated, "minute60", ema_len=5)
    assert (a[:-30] == b[:-30]).all()


def test_trend_series_is_false_before_first_completed_higher_bar():
    df = _synth(5, 3600)
    a = tfcore.trend_series(df, "minute60", ema_len=5)
    assert not a[:12].any()                     # 첫 1시간(5분봉 12개)은 판정 불가 → False


def test_trend_series_length_matches_input():
    df = _synth(5, 3600)
    assert len(tfcore.trend_series(df, "minute60", ema_len=5)) == len(df)


# ── 4. 판정 로직 ────────────────────────────────────────────────────
def test_daily_exp_multiplies_frequency():
    s = Stats(trades=200, exp=0.002)
    assert tfcore.daily_exp(s, days=100) == pytest.approx(0.004)   # 0.2% × 2회/일


def test_required_t_rises_with_number_of_comparisons():
    assert tfcore.required_t(1) == pytest.approx(2.0)
    assert tfcore.required_t(36) > 2.8


def test_judge_rejects_when_stress_expectancy_is_negative():
    """하한 ② — 왕복비용 0.25%에서 음수면 다른 지표가 좋아도 탈락."""
    oos = Stats(trades=300, exp=0.003, t_stat=4.0)
    stress = Stats(trades=300, exp=-0.0001, t_stat=-0.2)
    v = tfcore.judge(oos, stress, port_mdd=0.05, n_compared=1)
    assert not v.passed
    assert any("비용 스트레스" in r for r in v.reasons)


def test_judge_rejects_on_mdd_and_on_low_t():
    oos = Stats(trades=300, exp=0.003, t_stat=4.0)
    stress = Stats(trades=300, exp=0.001, t_stat=1.0)
    assert not tfcore.judge(oos, stress, port_mdd=0.25, n_compared=1).passed
    weak = Stats(trades=300, exp=0.003, t_stat=1.9)
    assert not tfcore.judge(weak, stress, port_mdd=0.05, n_compared=1).passed


def test_judge_passes_when_all_three_floors_met():
    oos = Stats(trades=300, exp=0.003, t_stat=4.0)
    stress = Stats(trades=300, exp=0.0008, t_stat=2.2)
    v = tfcore.judge(oos, stress, port_mdd=0.06, n_compared=1)
    assert v.passed and v.reasons == []


# ── 5. walk-forward 폴드 구성 ───────────────────────────────────────
def test_fold_bounds_oos_windows_do_not_overlap():
    """OOS 구간이 겹치면 같은 거래를 여러 번 세어 t값이 부풀려진다."""
    idx = pd.date_range("2024-01-01", periods=2 * 365 * 24 * 12, freq="5min")   # 2년 5분봉
    folds = tfcore.fold_bounds(idx, warmup=2580, is_days=139, oos_days=69)
    assert len(folds) >= 3
    prev_oos_start = None
    for is_lo, is_hi, oos_hi in folds:
        assert is_lo < is_hi < oos_hi
        if prev_oos_start is not None:
            assert is_hi >= prev_oos_start        # 이번 OOS 시작 ≥ 지난 OOS 시작
        prev_oos_start = is_hi
    starts = [f[1] for f in folds]
    assert starts == sorted(set(starts))          # OOS 시작이 중복 없이 전진


def test_fold_bounds_is_timeframe_independent():
    """폴드를 봉 수가 아니라 시간으로 잘라야 타임프레임 간 비교가 가능하다."""
    idx5 = pd.date_range("2024-01-01", periods=2 * 365 * 24 * 12, freq="5min")
    idx60 = pd.date_range("2024-01-01", periods=2 * 365 * 24, freq="60min")
    f5 = tfcore.fold_bounds(idx5, warmup=2580, is_days=139, oos_days=69)
    f60 = tfcore.fold_bounds(idx60, warmup=900, is_days=139, oos_days=69)
    assert abs(len(f5) - len(f60)) <= 1


def test_fold_bounds_returns_empty_when_data_too_short():
    idx = pd.date_range("2026-01-01", periods=1000, freq="5min")
    assert tfcore.fold_bounds(idx, warmup=100, is_days=139, oos_days=69) == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_tfcore.py -v`
Expected: FAIL — `ModuleNotFoundError` 또는 `ImportError: cannot import name 'lab_tfcore'`

- [ ] **Step 3: 구현**

```python
"""타임프레임 공통 코어 — 축 2·3·4·최종이 공유한다.

여기 한 곳에만 두는 이유: 비용·체결 모델이 두 군데 있으면 검증한 코드와 배포한 코드가
어긋난다(이전 세션에 실제로 겪었다 — 전체창 VWAP vs 라이브 200봉 창).

설계 결정
 - 시간손절은 모든 타임프레임에서 **8시간**으로 고정한다(현행 설계 의도 유지).
 - 변동성 게이트는 5분봉 0.6%를 기준으로 √봉길이로 환산한다. 15분→1.04%(헌장 1.0%),
   30분→1.47%, 60분→2.08%, 1분→0.27%. 엣지는 변동성에 비례하고 비용은 고정이므로
   봉이 길어질수록 요구 문턱도 올라간다.
 - 손절/익절은 √환산이 헌장값(5분 2.5% → 15분 3.0%)과 맞지 않으므로 **표로 명시**한다.
   가짜 공식을 만들지 않는다.
 - 추세 필터는 1~30분봉 전략은 1시간봉 EMA200(라이브와 동일), 60분봉 전략은 4시간봉
   EMA200을 쓴다. 진입봉과 필터봉이 같으면 필터가 아니다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import portfolio
from backtesting.research.fastsim import (ExitCfg, Precomp, Stats, simulate_arrays,
                                          stats_chrono)
from backtesting.research.lab_screen import ENTRY_DELAY

TF_MINUTES = {"minute1": 1, "minute3": 3, "minute5": 5, "minute10": 10,
              "minute15": 15, "minute30": 30, "minute60": 60, "minute240": 240}

TIME_STOP_MINUTES = 480          # 8시간 (헌장 §4 시간손절)
BASE_TF, BASE_GATE = "minute5", 0.006

# 손절/익절 — 봉 길이별 검증값·설계값 (√환산이 헌장값과 맞지 않으므로 표로 둔다)
STOP_BY_TF = {"minute1": 0.015, "minute3": 0.020, "minute5": 0.025, "minute10": 0.028,
              "minute15": 0.030, "minute30": 0.040, "minute60": 0.050}

RSI_EXIT_LEVEL = 70.0            # 라이브와 동일 (strategies/rsi2_pullback.EXIT_LEVEL)
STRESS_SPREAD = 0.0015           # 하한 ②: 수수료 0.1% + 이 값 = 왕복 0.25%
UPBIT_4H_OFFSET = "1h"           # 업비트 4시간봉 경계 01/05/09/13/17/21시(KST) 실측


@dataclass(frozen=True)
class TfParams:
    tf: str
    minutes: int
    gate: float
    stop: float
    time_stop_bars: int
    warmup: int
    trend_tf: str

    def label(self) -> str:
        return (f"{self.minutes}분 gate={self.gate:.2%} sl={self.stop:.1%} "
                f"ts={self.time_stop_bars}봉 추세={TF_MINUTES[self.trend_tf]}분")


def params_for(tf: str, gate: float | None = None, stop: float | None = None) -> TfParams:
    if tf not in TF_MINUTES:
        raise ValueError(f"알 수 없는 타임프레임: {tf}")
    m = TF_MINUTES[tf]
    trend_tf = "minute240" if m >= 60 else "minute60"
    auto_gate = BASE_GATE * math.sqrt(m / TF_MINUTES[BASE_TF])
    # EMA200 of trend_tf + 여유 5% + 지표 워밍업
    warmup = int(200 * TF_MINUTES[trend_tf] / m * 1.05) + 60
    return TfParams(tf=tf, minutes=m,
                    gate=auto_gate if gate is None else gate,
                    stop=STOP_BY_TF[tf] if stop is None else stop,
                    time_stop_bars=TIME_STOP_MINUTES // m,
                    warmup=warmup, trend_tf=trend_tf)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """더 짧은 봉을 tf로 집계. 업비트 실제 봉과 일치하도록 4시간만 offset을 준다."""
    m = TF_MINUTES[tf]
    kw = {"offset": UPBIT_4H_OFFSET} if m == 240 else {}
    out = df.resample(f"{m}min", **kw).agg({"open": "first", "high": "max", "low": "min",
                                            "close": "last", "volume": "sum"})
    return out.dropna()


def trend_series(df: pd.DataFrame, trend_tf: str, ema_len: int = 200) -> np.ndarray:
    """상위 타임프레임 EMA200 위 여부를 대상 봉 인덱스에 정렬.

    shift(1) 이 핵심이다 — **직전 완성봉**만 본다. 이걸 빼면 미래를 보고 진입하는
    백테스트가 되어 허구의 엣지가 나온다.
    """
    m = TF_MINUTES[trend_tf]
    kw = {"offset": UPBIT_4H_OFFSET} if m == 240 else {}
    h = df["close"].resample(f"{m}min", **kw).last().dropna()
    up = h > ta.ema(h, ema_len)
    aligned = up.shift(1).reindex(df.index, method="ffill")
    return aligned.astype(float).fillna(0.0).to_numpy(bool)


def signals(df: pd.DataFrame, th: float, gate: float,
            trend_tf: str) -> tuple[np.ndarray, np.ndarray]:
    """RSI(2) 눌림목 진입/청산 — 라이브 strategies/rsi2_pullback.py 와 같은 조건."""
    r2 = ta.rsi(df["close"], 2)
    atr_ratio = ta.atr(df) / df["close"]
    enter = ((r2 <= th).fillna(False).to_numpy(bool)
             & trend_series(df, trend_tf)
             & (atr_ratio >= gate).fillna(False).to_numpy(bool))
    exit_ = (r2 >= RSI_EXIT_LEVEL).fillna(False).to_numpy(bool) & ~enter
    return enter, exit_


def run_market(df: pd.DataFrame, p: TfParams, th: float, slip: float, *,
               start: int | None = None, end: int | None = None,
               market: str = "") -> tuple[list[tuple], list[portfolio.PortTrade]]:
    """한 종목 시뮬. 반환: (시간순집계용 (진입시각, 손익) 목록, 포트폴리오용 거래 목록)."""
    cfg = ExitCfg("pct", p.stop, 1.0, time_stop_bars=p.time_stop_bars, use_exit_signal=True)
    enter, exit_ = signals(df, th, p.gate, p.trend_tf)
    close, high, low = (df[c].to_numpy(float) for c in ("close", "high", "low"))
    atr, open_ = ta.atr(df).to_numpy(float), df["open"].to_numpy(float)
    pre = Precomp(enter, exit_, close, high, low, atr, open_)
    items, pts = [], []
    for t in simulate_arrays(pre, cfg, start=p.warmup if start is None else start,
                             end=len(df) if end is None else end,
                             slippage=slip, entry_delay=ENTRY_DELAY):
        items.append((df.index[t.entry_i], t.pnl))
        pts.append(portfolio.PortTrade(df.index[t.entry_i], df.index[t.exit_i], t.pnl,
                                       cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]),
                                       market))
    return items, pts


def daily_exp(s: Stats, days: int) -> float:
    """일일 기댓값 = 거래당 기댓값 × 하루 거래수 (운영자가 고른 목적함수)."""
    if days <= 0 or s.trades == 0:
        return 0.0
    return s.exp * (s.trades / days)


def required_t(n_compared: int) -> float:
    """다중비교 보정된 필요 t.

    n개 조합을 비교해 최고를 골랐다면 그 t는 부풀려져 있다. Bonferroni 방식으로
    유의수준을 n으로 나눈 것에 해당하는 정규분위수를 쓴다(t≈z, 표본 200+ 가정).
    선례: 16조합 → 약 2.9.
    """
    from statistics import NormalDist
    n = max(1, int(n_compared))
    if n == 1:
        return 2.0
    alpha = 0.05 / n
    return max(2.0, NormalDist().inv_cdf(1 - alpha / 2))


def fold_bounds(idx: pd.DatetimeIndex, warmup: int, is_days: int = 139,
                oos_days: int = 69) -> list[tuple[int, int, int]]:
    """rolling walk-forward 폴드 (is_lo, is_hi, oos_hi) 인덱스 위치 목록.

    **시간 기준**으로 자른다(봉 수 기준이면 타임프레임마다 기간이 달라져 비교가 안 된다).
    OOS 구간은 겹치지 않는다 — 겹치면 같은 거래를 여러 번 세어 t가 부풀려진다.
    """
    if warmup >= len(idx):
        return []
    out: list[tuple[int, int, int]] = []
    t0 = idx[warmup]
    while True:
        is_lo = int(idx.searchsorted(t0))
        is_hi = int(idx.searchsorted(t0 + pd.Timedelta(days=is_days)))
        oos_hi = int(idx.searchsorted(t0 + pd.Timedelta(days=is_days + oos_days)))
        if oos_hi >= len(idx) or is_hi <= is_lo:
            break
        out.append((is_lo, is_hi, oos_hi))
        t0 = t0 + pd.Timedelta(days=oos_days)
    return out


def walk_forward(panel: dict, tf: str, grid: list[tuple[float, float]],
                 spreads: dict, *, is_days: int = 139, oos_days: int = 69,
                 min_is_trades: int = 50) -> tuple[Stats, list, list[tuple], int]:
    """폴드마다 IS에서 파라미터를 재선택해 다음 OOS에 적용한다 — 하한 ①의 주 증거.

    사후선택이 구조적으로 불가능하므로 홀드아웃 한 번보다 강한 증거다. 문턱 완화가
    진짜 이득이면 여기서 나타난다(이전 세션에서 IS 최고였던 th=10은 여기서 무너졌다).

    선택 규칙(사전 등록): 해당 폴드 IS에서 거래 ≥ min_is_trades 인 조합 중 t 최대.
    반환: (OOS 합계 Stats, 포트폴리오 거래 목록, 폴드별 (th, gate) 선택 기록, OOS 일수)
    """
    if not panel:
        return Stats(), [], [], 0
    idx = next(iter(panel.values())).index
    base = params_for(tf)
    folds = fold_bounds(idx, base.warmup, is_days, oos_days)
    if not folds:
        return Stats(), [], [], 0

    def span(th: float, gate: float, lo: int, hi: int):
        items, pts = [], []
        p = params_for(tf, gate=gate)
        for m, df in panel.items():
            i, t = run_market(df, p, th, spreads.get(m, 0.001),
                              start=max(lo, p.warmup), end=hi, market=m)
            items += i
            pts += t
        return items, pts

    oos_items, oos_pts, picks = [], [], []
    for is_lo, is_hi, oos_hi in folds:
        best = None
        for th, gate in grid:
            s = stats_chrono(span(th, gate, is_lo, is_hi)[0])
            if s.trades >= min_is_trades and (best is None or s.t_stat > best[0]):
                best = (s.t_stat, th, gate)
        if best is None:
            picks.append((idx[is_hi], None, None))
            continue
        _, th, gate = best
        i, t = span(th, gate, is_hi, oos_hi)
        oos_items += i
        oos_pts += t
        picks.append((idx[is_hi], th, gate))
    days = (idx[folds[-1][2] - 1] - idx[folds[0][1]]).days
    return stats_chrono(oos_items), oos_pts, picks, max(days, 1)


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def judge(oos: Stats, stress: Stats, port_mdd: float, n_compared: int) -> Verdict:
    """사전 등록한 하한 3개. 사후에 느슨하게 바꾸지 않는다."""
    need_t = required_t(n_compared)
    reasons: list[str] = []
    if not (oos.exp > 0):
        reasons.append(f"① OOS 기댓값 음수 ({oos.exp:+.3%})")
    if not (oos.t_stat > need_t):
        reasons.append(f"① t 부족 ({oos.t_stat:+.2f} ≤ 필요 {need_t:.2f}, {n_compared}조합 보정)")
    if not (stress.exp > 0):
        reasons.append(f"② 비용 스트레스(왕복 0.25%)에서 음수 ({stress.exp:+.3%})")
    if not (port_mdd <= 0.20):
        reasons.append(f"③ 포트폴리오 MDD 초과 ({port_mdd:.1%} > 20%)")
    return Verdict(passed=not reasons, reasons=reasons)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_tfcore.py -v`
Expected: PASS (파라미터 5 + 환산 4 + 리샘플 2 + lookahead 3 + 판정 5 + 폴드 3). 리샘플 테스트 2개는 캐시가 있으면 PASS, 없으면 SKIP.

만약 `test_gate_and_stop_match_live_charter_for_5m_and_15m`이 실패하면 게이트 환산식이 헌장값과 어긋난 것이다 — 공식을 바꾸지 말고 `abs` 허용치가 아니라 실제 값을 확인하고 보고할 것.

- [ ] **Step 5: 기존 테스트가 깨지지 않았는지 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`
Expected: 기존 테스트 전부 PASS(신규 2파일 포함)

- [ ] **Step 6: 라이브 신호와의 일치 확인 (가장 중요한 관문)**

`lab_tfcore.signals`가 라이브 전략 클래스와 같은 신호를 내는지 확인한다. 기존 관문 스크립트를 그대로 쓴다.

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_live_parity.py`
Expected: 불일치 0건. 이 스크립트는 `lab_15m` 경로를 쓰므로 여전히 통과해야 한다(회귀 확인).

추가로 `lab_tfcore`가 `lab_15m`과 같은 신호를 내는지 직접 비교한다.

```bash
cd upbit-rbi-bot && .venv/bin/python - <<'PY'
import numpy as np
from backtesting.research import data_cache, lab_15m, lab_tfcore as tfcore
df5 = data_cache.load("KRW-BTC", "minute5")
df15 = lab_15m.to_15m(df5)
a_e, a_x = lab_15m.signals(df15, 3.0, 0.010)
p = tfcore.params_for("minute15")
b_e, b_x = tfcore.signals(df15, 3.0, p.gate, p.trend_tf)
print("진입 불일치", int((a_e != b_e).sum()), "청산 불일치", int((a_x != b_x).sum()))
PY
```
Expected: 게이트가 0.010 vs 0.0104로 미세하게 다르므로 진입 불일치가 0이 아닐 수 있다. **`gate=0.010`을 명시로 넘겨 재실행**하면 진입·청산 불일치가 모두 0이어야 한다. 0이 아니면 구현이 다른 것이므로 원인을 찾아 보고할 것.

- [ ] **Step 7: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/lab_tfcore.py upbit-rbi-bot/tests/test_tfcore.py
git commit -m "feat(research): 타임프레임 공통 코어(파라미터 환산·리샘플·추세·판정)"
```

---

### Task 4: 데이터 수집 실행 (API · 단독 실행)

**Files:**
- 코드 변경 없음. Task 1·2에서 만든 도구를 실행한다.
- 산출물: `data/spreads_opt.json`, `data/spreads_cons.json`, `data/KRW-*_minute1.csv`, `data/KRW-VANA_minute5.csv`, `data/KRW-FIL_minute5.csv`

**Interfaces:**
- Consumes: Task 1의 `--sample2`, Task 2의 `min1` 모드
- Produces: 이후 모든 축이 읽는 데이터 파일

⚠️ **각 스텝을 순차로, 단독으로 실행한다.** 두 개를 동시에 돌리면 레이트리밋으로 0봉 실패한다.

- [ ] **Step 1: 스프레드 재측정 (약 5분)**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/spread_check.py --sample2 mid`
그 다음: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/spread_check.py --sample2`

두 번 돌리는 이유: `MARKETS_MID`(중형)와 `MARKETS_LAB`(대형+알트)이 다른 목록이다. 두 번째 실행이 첫 결과를 덮어쓰므로, **첫 실행 결과를 먼저 다른 이름으로 복사**한다.

```bash
cd upbit-rbi-bot
.venv/bin/python backtesting/research/spread_check.py --sample2 mid
cp backtesting/research/data/spreads_opt.json  backtesting/research/data/spreads_opt_mid.json
cp backtesting/research/data/spreads_cons.json backtesting/research/data/spreads_cons_mid.json
.venv/bin/python backtesting/research/spread_check.py --sample2
```

Expected: 두 벌씩 총 4개 파일. 대형 종목 보수값이 0.15% 내외, 중형이 0.2~0.6% 범위로 나올 것으로 예상.

- [ ] **Step 2: 24시간 관측 시작 (백그라운드)**

Run(백그라운드): `cd upbit-rbi-bot && .venv/bin/python backtesting/research/spread_check.py --watch mid`

24시간 뒤에 결과가 나오므로 이번 작업의 진행을 막지 않는다. 결과가 나오면 Task 9에서 보수값을 실측으로 대체한다. **이 프로세스는 10분 간격이므로 다른 API 작업과 충돌하지 않는다**(호출 1회/10분).

- [ ] **Step 3: 1분봉 2년 수집 (약 2.2시간, 단독)**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/data_cache.py min1`
Expected: 6종목 × 약 105만 봉. 청크마다 진행률이 찍힌다. 중단되면 같은 명령을 재실행하면 이어받는다.

확인:
```bash
cd upbit-rbi-bot && .venv/bin/python - <<'PY'
from backtesting.research import data_cache
for m in data_cache.MARKETS_1M:
    d = data_cache.load(m, "minute1")
    print(f"{m:10s} {0 if d is None else len(d):9,d}봉 "
          f"{'' if d is None else f'{d.index[0].date()}~{d.index[-1].date()}'}")
PY
```
Expected: 각 종목 100만 봉 이상, 시작일이 2024-07 이전(홀드아웃 경계 2025-12-29보다 충분히 앞).

- [ ] **Step 4: VANA·FIL 5분봉 수집**

`data_cache.py`의 `MARKETS_MID`에 두 종목을 추가한다.

```python
MARKETS_MID = ["KRW-SUI", "KRW-ATOM", "KRW-ENS", "KRW-GAS", "KRW-KAITO",
               "KRW-NEAR", "KRW-TRUMP", "KRW-ORCA",
               "KRW-UNI", "KRW-AAVE", "KRW-PENDLE",
               # v2.2 유니버스에 나타난 미검증 종목 (백로그 우선순위 1)
               "KRW-VANA", "KRW-FIL"]
```

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/data_cache.py mid`
Expected: 기존 11종목은 `skip (캐시)`, VANA·FIL만 새로 수집. 상장 기간이 짧으면 `.complete` 마커가 생기고 다음 실행부터 재수집하지 않는다.

- [ ] **Step 5: 1분봉 리샘플이 실제 5분봉과 일치하는지 확인**

이것이 1분봉 데이터를 신뢰할 수 있는지 판정하는 관문이다.

```bash
cd upbit-rbi-bot && .venv/bin/python - <<'PY'
import numpy as np
from backtesting.research import data_cache, lab_tfcore as tfcore
for m in data_cache.MARKETS_1M:
    d1, d5 = data_cache.load(m, "minute1"), data_cache.load(m, "minute5")
    if d1 is None or d5 is None:
        print(f"{m}: 캐시 없음"); continue
    got = tfcore.resample(d1, "minute5")
    common = got.index.intersection(d5.index)[1:-1]
    bad = (~np.isclose(got.loc[common, "close"], d5.loc[common, "close"], rtol=1e-9)).sum()
    print(f"{m:10s} 겹치는 봉 {len(common):7,d}  불일치 {bad}")
PY
```
Expected: 모든 종목 불일치 0. 하나라도 0이 아니면 수집 데이터에 결측·정렬 문제가 있으므로 **다음 태스크로 넘어가지 말고 보고**한다.

- [ ] **Step 6: Commit**

```bash
cd /Users/jasonair/Projects/life-change
git add upbit-rbi-bot/backtesting/research/data_cache.py
git add upbit-rbi-bot/backtesting/research/data/spreads_opt.json \
        upbit-rbi-bot/backtesting/research/data/spreads_cons.json \
        upbit-rbi-bot/backtesting/research/data/spreads_opt_mid.json \
        upbit-rbi-bot/backtesting/research/data/spreads_cons_mid.json
git commit -m "chore(research): 스프레드 낙관/보수 재측정 + VANA·FIL 추가"
```

1분봉 CSV는 커밋하지 않는다(수백 MB). `.gitignore`에 `backtesting/research/data/*_minute1.csv`가 없으면 추가한다.

---

### Task 5: 축 2 — 문턱 2D frontier (`lab_frontier.py`)

**Files:**
- Create: `backtesting/research/lab_frontier.py`

**Interfaces:**
- Consumes: `lab_tfcore.{params_for, resample, run_market, daily_exp, judge, required_t, walk_forward, STRESS_SPREAD}`, `lab.load_panel`, `lab_final.SELECTION_START`
- Produces: 표준출력 표 + `data/frontier_<tf>.json`(조합별 지표, Task 8이 읽는다)

- [ ] **Step 1: 스크립트 작성**

```python
"""축 2 — 문턱 완화의 대가를 표로 확정한다.

운영자 질문: "RSI(2)≤3, ATR≥0.6% 가 너무 타이트한가. 값을 풀면 빈도가 늘고 손해는 없는가."

이미 아는 것(재확인에 시간을 쓰지 않는다):
 - RSI(2)≤10 은 홀드아웃에서 −0.052%(t−3.01)로 반전했다 (lab_final.py)
 - ATR 게이트 0.6%→0.3% 는 +0.199%→−0.050% (lab_screen.py)
따라서 목적은 '완화가 되는가'가 아니라 **'어디까지 풀면 일일 기댓값이 최대인가'**다.

방법:
 - 선택구간(2025-12-29~)에서 36조합(th 6 × gate 6)을 훑어 표를 만든다
 - 홀드아웃은 **최종 후보 1개에만** 적용한다(조회 예산 1회)
 - 비용은 낙관/보수 두 벌 + 왕복 0.25% 스트레스

실행: .venv/bin/python backtesting/research/lab_frontier.py [minute5|minute15]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import charter as C
from backtesting.research import lab, lab_tfcore as tfcore, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import stats_chrono
from backtesting.research.lab_final import SELECTION_START

TH_GRID = [2.0, 3.0, 5.0, 7.0, 10.0, 15.0]
GATE_MULT = [0.67, 0.83, 1.0, 1.33, 1.67, 2.0]      # 기준 게이트 대비 배수
N_COMPARED = len(TH_GRID) * len(GATE_MULT)


def load_spreads(name: str) -> dict[str, float]:
    """낙관/보수 스프레드. 대형·중형 두 파일을 합치고 같은 종목은 더 넓은 값을 쓴다."""
    out: dict[str, float] = {}
    for fn in (f"spreads_{name}.json", f"spreads_{name}_mid.json"):
        p = DATA_DIR / fn
        if p.exists():
            for m, v in json.loads(p.read_text()).items():
                out[m] = max(out.get(m, 0.0), float(v))
    return out


def panel_for(tf: str, tradable: set[str]) -> dict:
    p5 = lab.load_panel("minute5")
    if tf == "minute5":
        return {m: d for m, d in p5.items() if m in tradable}
    return {m: tfcore.resample(d, tf) for m, d in p5.items() if m in tradable}


def sweep(panel: dict, tf: str, th: float, gate: float, spreads: dict,
          lo: int, hi: int | None) -> tuple:
    p = tfcore.params_for(tf, gate=gate)
    items, pts = [], []
    for m, df in panel.items():
        i, t = tfcore.run_market(df, p, th, spreads[m], start=max(lo, p.warmup),
                                 end=hi, market=m)
        items += i
        pts += t
    return stats_chrono(items), pts


def main() -> None:
    tf = next((a for a in sys.argv[1:] if a.startswith("minute")), "minute5")
    opt, cons = load_spreads("opt"), load_spreads("cons")
    if not opt:
        print("스프레드 측정값 없음 — spread_check.py --sample2 먼저 실행")
        return
    tradable = {m for m, v in opt.items() if v <= C.MAX_SPREAD_RATIO}
    panel = panel_for(tf, tradable)
    if not panel:
        print(f"{tf} 패널 없음 — data_cache 확인")
        return
    idx = next(iter(panel.values())).index
    base = tfcore.params_for(tf)
    split = int(idx.searchsorted(SELECTION_START))
    sel_days = max(1, (idx[-1] - idx[split]).days)

    print(f"축 2 문턱 frontier · {tf}({base.minutes}분) · {len(panel)}종목 "
          f"· 기준 게이트 {base.gate:.2%} 손절 {base.stop:.1%}")
    print(f"선택구간 {idx[split].date()}~{idx[-1].date()} ({sel_days}일) "
          f"· 홀드아웃은 최종 후보에만 1회 적용")
    print(f"비용 = 수수료 {C.FEE_ROUNDTRIP:.1%} + 종목별 스프레드 · 다음봉 시가 진입")
    print(f"목적함수 = 일일 기댓값(거래당 exp × 하루 거래수) · {N_COMPARED}조합 "
          f"→ 필요 t {tfcore.required_t(N_COMPARED):.2f}\n")
    print(f"{'th':>4s} {'gate':>7s} │ {'거래':>6s} {'하루':>5s} {'승률':>6s} {'PF':>5s} "
          f"{'exp':>8s} {'일일exp':>8s} {'t':>6s} │ {'보수exp':>8s} {'0.25%exp':>9s}")

    rows = []
    for th, mult in product(TH_GRID, GATE_MULT):
        gate = base.gate * mult
        s_opt, pts = sweep(panel, tf, th, gate, opt, 0, None)
        if s_opt.trades == 0:
            continue
        s_cons, _ = sweep(panel, tf, th, gate, cons, 0, None)
        stress = {m: tfcore.STRESS_SPREAD for m in panel}
        s_str, _ = sweep(panel, tf, th, gate, stress, 0, None)
        d_exp = tfcore.daily_exp(s_opt, sel_days)
        pf = "  inf" if s_opt.profit_factor == float("inf") else f"{s_opt.profit_factor:5.2f}"
        rows.append({"th": th, "gate": gate, "trades": s_opt.trades,
                     "per_day": s_opt.trades / sel_days, "exp": s_opt.exp,
                     "daily_exp": d_exp, "t": s_opt.t_stat,
                     "exp_cons": s_cons.exp, "exp_stress": s_str.exp,
                     "win_rate": s_opt.win_rate, "pf": s_opt.profit_factor})
        print(f"{th:4.0f} {gate:7.2%} │ {s_opt.trades:6d} {s_opt.trades/sel_days:5.2f} "
              f"{s_opt.win_rate:6.1%} {pf} {s_opt.exp:+8.3%} {d_exp:+8.3%} "
              f"{s_opt.t_stat:+6.2f} │ {s_cons.exp:+8.3%} {s_str.exp:+9.3%}")

    if not rows:
        print("\n거래 0건 — 게이트 범위를 확인할 것")
        return
    (DATA_DIR / f"frontier_{tf}.json").write_text(json.dumps(rows, indent=2))

    # 하한 ②를 통과하는 것 중 일일 기댓값 최대
    alive = [r for r in rows if r["exp_stress"] > 0 and r["exp_cons"] > 0]
    print(f"\n비용 스트레스(왕복 0.25%)·보수 스프레드 양쪽 생존: {len(alive)}/{len(rows)}조합")
    if not alive:
        print("❌ 문턱 완화로 생존하는 조합 없음 — 현행 값 유지가 정합적")
        return
    best = max(alive, key=lambda r: r["daily_exp"])
    cur = next((r for r in rows if r["th"] == 3.0 and abs(r["gate"] - base.gate) < 1e-9), None)
    print(f"최고: th={best['th']:.0f} gate={best['gate']:.2%} "
          f"일일exp {best['daily_exp']:+.3%} (하루 {best['per_day']:.2f}회)")
    if cur:
        print(f"현행: th=3 gate={cur['gate']:.2%} "
              f"일일exp {cur['daily_exp']:+.3%} (하루 {cur['per_day']:.2f}회)")
        print(f"→ 빈도 {best['per_day']/max(cur['per_day'],1e-9):.2f}배, "
              f"일일 기댓값 {best['daily_exp']-cur['daily_exp']:+.3%}p")

    # 하한 ①의 주 증거 = walk-forward (폴드마다 그리드 재선택)
    grid = [(r["th"], r["gate"]) for r in rows]
    wf, wf_pts, picks, wf_days = tfcore.walk_forward(panel, tf, grid, cons)
    wf_stress, _, _, _ = tfcore.walk_forward(
        panel, tf, grid, {m: tfcore.STRESS_SPREAD for m in panel})
    wr = portfolio.run(wf_pts) if wf_pts else None
    wv = tfcore.judge(wf, wf_stress, wr.mdd if wr else 1.0, N_COMPARED)
    print(f"\n── walk-forward ({len(picks)}폴드 · 폴드마다 재선택) ─────────")
    print(f"OOS {wf.trades}거래 / {wf_days}일 = 하루 {wf.trades/wf_days:.2f}회 · "
          f"승률 {wf.win_rate:.1%} PF {wf.profit_factor:.2f}")
    print(f"exp {wf.exp:+.3%} t{wf.t_stat:+.2f} · 일일 기댓값 "
          f"{tfcore.daily_exp(wf, wf_days):+.3%} · 0.25%exp {wf_stress.exp:+.3%} · "
          f"MDD {wr.mdd if wr else 0:.1%}")
    chosen = [f"th={th:.0f}/{gate:.2%}" for _, th, gate in picks if th is not None]
    print(f"폴드별 선택: {', '.join(chosen) or '없음(IS 거래 부족)'}")
    print(f"판정(하한 3개): {'✅ 통과' if wv.passed else '❌ ' + ' / '.join(wv.reasons)}")
    if not wv.passed:
        print("→ walk-forward에서 탈락. 홀드아웃을 태우지 않고 여기서 끝낸다"
              "(조회 예산 0회 사용).")
        return

    # 홀드아웃 조회 1회 — walk-forward를 통과한 최종 후보에만
    p = tfcore.params_for(tf, gate=best["gate"])
    h_items, h_pts = [], []
    for m, df in panel.items():
        i, t = tfcore.run_market(df, p, best["th"], cons[m],
                                 start=p.warmup, end=split, market=m)
        h_items += i
        h_pts += t
    sh = stats_chrono(h_items)
    r = portfolio.run(h_pts) if h_pts else None
    print(f"\n── 홀드아웃 조회 1회 (보수 스프레드 · 확인용) ─────────────")
    print(f"{sh.trades}거래 승률 {sh.win_rate:.1%} PF {sh.profit_factor:.2f} "
          f"exp {sh.exp:+.3%} t{sh.t_stat:+.2f} · 계좌 "
          f"{r.total_return if r else 0:+.1%} MDD {r.mdd if r else 0:.1%}")
    print("※ 판정은 위 walk-forward가 정본이다. 이 줄은 확인용이며 "
          "여기 숫자가 나쁘다고 파라미터를 다시 고르면 홀드아웃을 태우는 것이다.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 5분봉 실행**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_frontier.py minute5`
Expected: 36행 표. 게이트가 낮은 행에서 거래수가 늘고 `0.25%exp` 열이 음수로 바뀌는 패턴이 보일 것. `frontier_minute5.json` 생성.

- [ ] **Step 3: 15분봉 실행**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_frontier.py minute15`
Expected: 동일 형식. `frontier_minute15.json` 생성.

- [ ] **Step 4: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/lab_frontier.py \
        upbit-rbi-bot/backtesting/research/data/frontier_minute5.json \
        upbit-rbi-bot/backtesting/research/data/frontier_minute15.json
git commit -m "feat(research): 축2 문턱 2D frontier — 완화의 대가를 일일 기댓값으로 정량화"
```

---

### Task 6: 축 3 — 타임프레임 스캔 (`lab_tfscan.py`)

**Files:**
- Create: `backtesting/research/lab_tfscan.py`

**Interfaces:**
- Consumes: `lab_tfcore.*`, `lab_frontier.load_spreads`, `data_cache.load`
- Produces: 표준출력 표 + `data/tfscan.json` (타임프레임별 지표 · Task 8이 읽는다)

- [ ] **Step 1: 스크립트 작성**

```python
"""축 3 — 타임프레임 확장이 빈도의 정당한 출처인가.

근거: 왕복비용/한 봉 평균움직임 = 5분 77% · 15분 46% · 1시간 20%. 봉이 길수록 비용 부담이
작다. 그래서 5분+15분 병행이 t를 3.48→5.06으로 올렸다(lab_combined.py). 그 방향을 한 단계 더
밀어 30분·60분을 넣고, 반대 방향인 1분봉이 정말 불가능한지도 실측한다.

1분봉 사전 예상: 한 봉 평균움직임 ≈ 0.088%(5분봉 0.196%의 1/√5), 왕복비용 0.15%
 → 비용/움직임 ≈ 171%. 기각이 예상되지만 숫자로 확인한다.

실행: .venv/bin/python backtesting/research/lab_tfscan.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from backtesting.research import data_cache, lab_tfcore as tfcore, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import stats_chrono
from backtesting.research.lab_frontier import load_spreads
from backtesting.research.lab_final import SELECTION_START

TFS = ["minute1", "minute5", "minute15", "minute30", "minute60"]
N_COMPARED = len(TFS)


def source_for(market: str, tf: str):
    """가장 정확한 원천을 고른다: 1분봉 캐시 → 5분봉 캐시 리샘플."""
    if tf == "minute1":
        return data_cache.load(market, "minute1")
    d1 = data_cache.load(market, "minute1")
    if d1 is not None and len(d1) > 500_000:
        return tfcore.resample(d1, tf)
    d5 = data_cache.load(market, "minute5")
    if d5 is None:
        return None
    return d5 if tf == "minute5" else tfcore.resample(d5, tf)


def bar_move(df) -> float:
    """한 봉 평균 절대 움직임(|종가 변화율|) — 비용 부담 비율의 분모."""
    return float(df["close"].pct_change().abs().mean())


def main() -> None:
    opt, cons = load_spreads("opt"), load_spreads("cons")
    if not opt:
        print("스프레드 측정값 없음 — spread_check.py --sample2 먼저 실행")
        return
    markets = [m for m in data_cache.MARKETS_1M if opt.get(m, 1.0) <= C.MAX_SPREAD_RATIO]
    print(f"축 3 타임프레임 스캔 · {len(markets)}종목 "
          f"({', '.join(m.replace('KRW-','') for m in markets)})")
    print(f"비용 = 수수료 {C.FEE_ROUNDTRIP:.1%} + 보수 스프레드 · 다음봉 시가 진입 "
          f"· {N_COMPARED}조합 → 필요 t {tfcore.required_t(N_COMPARED):.2f}\n")
    print(f"{'TF':>6s} {'봉움직임':>8s} {'비용/움직임':>11s} │ {'거래':>6s} {'하루':>5s} "
          f"{'승률':>6s} {'PF':>5s} {'exp':>8s} {'일일exp':>8s} {'t':>6s} │ "
          f"{'0.25%exp':>9s} {'MDD':>6s}")

    out = []
    for tf in TFS:
        p = tfcore.params_for(tf)
        items, pts, s_items, moves, days = [], [], [], [], 0
        for m in markets:
            df = source_for(m, tf)
            if df is None or len(df) < p.warmup + 500:
                continue
            split = int(df.index.searchsorted(SELECTION_START))
            days = max(days, (df.index[-1] - df.index[max(p.warmup, 0)]).days)
            moves.append(bar_move(df))
            i, t = tfcore.run_market(df, p, 3.0, cons.get(m, 0.001), market=m)
            items += i
            pts += t
            si, _ = tfcore.run_market(df, p, 3.0, tfcore.STRESS_SPREAD, market=m)
            s_items += si
        if not items:
            print(f"{p.minutes:5d}분 {'':8s} {'':11s} │ 거래 0건 또는 데이터 부족")
            continue
        s, ss = stats_chrono(items), stats_chrono(s_items)
        r = portfolio.run(pts)
        mv = float(np.mean(moves))
        ratio = (C.FEE_ROUNDTRIP + float(np.mean([cons.get(m, 0.001) for m in markets]))) / mv
        d_exp = tfcore.daily_exp(s, days)
        pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
        out.append({"tf": tf, "minutes": p.minutes, "bar_move": mv, "cost_ratio": ratio,
                    "trades": s.trades, "per_day": s.trades / max(days, 1), "exp": s.exp,
                    "daily_exp": d_exp, "t": s.t_stat, "exp_stress": ss.exp,
                    "mdd": r.mdd, "total_return": r.total_return,
                    "win_rate": s.win_rate, "pf": s.profit_factor})
        print(f"{p.minutes:5d}분 {mv:8.3%} {ratio:11.0%} │ {s.trades:6d} "
              f"{s.trades/max(days,1):5.2f} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} "
              f"{d_exp:+8.3%} {s.t_stat:+6.2f} │ {ss.exp:+9.3%} {r.mdd:6.1%}")

    (DATA_DIR / "tfscan.json").write_text(json.dumps(out, indent=2))
    alive = [r for r in out if r["exp_stress"] > 0 and r["exp"] > 0]
    print(f"\n비용 스트레스 생존 타임프레임: "
          f"{', '.join(str(r['minutes']) + '분' for r in alive) or '없음'}")
    dead = [r for r in out if r not in alive]
    for r in dead:
        print(f"  {r['minutes']}분 탈락 — exp {r['exp']:+.3%} / 0.25%exp {r['exp_stress']:+.3%} "
              f"(비용/움직임 {r['cost_ratio']:.0%})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_tfscan.py`
Expected: 5행(1·5·15·30·60분). `비용/움직임` 열이 1분봉에서 150% 이상으로 나오고 1분봉 exp가 음수일 것으로 예상. 30·60분봉은 거래수가 적지만 거래당 exp가 클 것으로 예상.

⚠️ 1분봉은 종목당 105만 봉이므로 이 스크립트가 몇 분 걸린다. 정상이다.

- [ ] **Step 3: 생존 타임프레임에 대해 축 2 재실행**

Step 2에서 생존한 타임프레임(예: `minute30`)에 대해 문턱 frontier를 돌린다.

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_frontier.py minute30`

`lab_frontier.panel_for`는 5분봉 리샘플만 지원하므로, `minute30`·`minute60`에서도 동작하는지 확인한다. `panel_for`가 `tfcore.resample(d, tf)`를 쓰므로 그대로 동작해야 한다. 에러가 나면 `panel_for`를 고친다.

- [ ] **Step 4: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/lab_tfscan.py \
        upbit-rbi-bot/backtesting/research/data/tfscan.json
git commit -m "feat(research): 축3 타임프레임 스캔(1·5·15·30·60분) — 비용/움직임 비율 실측"
```

---

### Task 7: 축 4 — 미검증 종목 판정

**Files:**
- Modify: `backtesting/research/lab_universe.py`

**Interfaces:**
- Consumes: `lab_tfcore.{params_for, run_market, judge}`, `lab_frontier.load_spreads`
- Produces: 표준출력 종목별 판정 + `data/universe_verdict.json`

- [ ] **Step 1: `load_spreads`를 낙관/보수 두 벌로 교체**

기존 `lab_universe.load_spreads()`는 `spreads.json`·`spreads_mid.json`을 합쳐 더 넓은 값을 쓴다. 새 파일 체계로 바꾸되, 새 파일이 없으면 기존 파일로 폴백한다(과거 재현성).

```python
def load_spread_pair() -> tuple[dict[str, float], dict[str, float]]:
    """(낙관, 보수) 두 벌. 새 파일이 없으면 기존 spreads*.json 으로 폴백한다."""
    from backtesting.research.lab_frontier import load_spreads
    opt, cons = load_spreads("opt"), load_spreads("cons")
    if opt:
        return opt, cons
    legacy = load_spreads_legacy()      # 기존 함수를 이 이름으로 rename
    return legacy, {m: v * 2.0 for m, v in legacy.items()}
```

기존 `load_spreads()` 함수 이름을 `load_spreads_legacy()`로 바꾸고, docstring에 "새 체계는 `load_spread_pair()`"를 적는다.

이 파일의 import 절에 `from backtesting.research import lab_tfcore as tfcore` 를 추가한다. 기존 import(`json`, `DATA_DIR`, `stats_chrono`, `portfolio`, `C`, `data_cache`)는 그대로 쓴다. 더 이상 쓰지 않는 `lab_15m`·`lab_screen`·`ta`·`fastsim.{ExitCfg, Precomp, simulate_arrays}` import는 제거한다(신호 구현이 `lab_tfcore`로 옮겨갔으므로 남겨두면 중복 구현이 다시 생긴다).

- [ ] **Step 2: `CONFIGS`를 `lab_tfcore`로 교체하고 낙관/보수 병기**

`CONFIGS` 상수와 `sig`·`run_market` 지역 함수를 지우고 `lab_tfcore`를 쓴다. 중복 구현이 두 군데 있으면 어긋난다.

```python
TFS = ["minute5", "minute15"]           # 축 3에서 생존한 타임프레임을 추가한다


def run_one(df, tf: str, slip: float, market: str):
    p = tfcore.params_for(tf)
    return tfcore.run_market(df, p, 3.0, slip, market=market)
```

`main()`의 종목 루프를 다음으로 바꾼다.

```python
    opt, cons = load_spread_pair()
    targets = sorted(m for m, v in opt.items() if v <= C.MAX_SPREAD_RATIO)
    print(f"대상 {len(targets)}종목 (낙관 스프레드 중앙값 ≤{C.MAX_SPREAD_RATIO:.1%})")
    print("라이브와 동일 파라미터(추가 튜닝 없음 = 이 종목들에는 전 구간 OOS)")
    print(f"비용 = 수수료 {C.FEE_ROUNDTRIP:.1%} + 스프레드 · 다음봉 시가 진입\n")
    print(f"{'종목':8s} {'낙관':>7s} {'보수':>7s} {'봉':>4s} {'거래':>6s} {'승률':>6s} "
          f"{'exp(낙관)':>10s} {'exp(보수)':>10s} {'t':>6s}  판정")

    verdict: dict[str, dict] = {}
    for m in targets:
        df5 = data_cache.load(m, "minute5")
        if df5 is None or len(df5) < 20_000:
            print(f"{m.replace('KRW-',''):8s} 데이터 부족 — 건너뜀")
            continue
        for tf in TFS:
            df = df5 if tf == "minute5" else tfcore.resample(df5, tf)
            i_opt, pts = run_one(df, tf, opt[m], m)
            i_con, _ = run_one(df, tf, cons.get(m, opt[m] * 2), m)
            s_opt, s_con = stats_chrono(i_opt), stats_chrono(i_con)
            if s_opt.trades == 0:
                print(f"{m.replace('KRW-',''):8s} {opt[m]:7.3%} "
                      f"{cons.get(m, 0):7.3%} {tfcore.TF_MINUTES[tf]:3d}분 "
                      f"{0:6d}   거래 없음")
                continue
            mark = "✅" if s_con.exp > 0 else ("△낙관만" if s_opt.exp > 0 else "❌")
            verdict.setdefault(m, {})[tf] = {
                "trades": s_opt.trades, "exp_opt": s_opt.exp, "exp_cons": s_con.exp,
                "t": s_opt.t_stat, "win_rate": s_opt.win_rate}
            print(f"{m.replace('KRW-',''):8s} {opt[m]:7.3%} {cons.get(m, 0):7.3%} "
                  f"{tfcore.TF_MINUTES[tf]:3d}분 {s_opt.trades:6d} {s_opt.win_rate:6.1%} "
                  f"{s_opt.exp:+10.3%} {s_con.exp:+10.3%} {s_opt.t_stat:+6.2f}  {mark}")
```

- [ ] **Step 3: 판정 요약과 JSON 저장 추가**

`main()` 끝에 붙인다. 보수 스프레드에서 음수면 차단 후보다.

```python
    print("\n── 종목별 판정 " + "─" * 46)
    keep, tf_only, drop = [], [], []
    for m, rows in verdict.items():
        tot = sum(r["trades"] for r in rows.values())
        w_opt = sum(r["exp_opt"] * r["trades"] for r in rows.values()) / max(tot, 1)
        w_con = sum(r["exp_cons"] * r["trades"] for r in rows.values()) / max(tot, 1)
        neg_tf = [tf for tf, r in rows.items() if r["exp_cons"] <= 0]
        if w_con > 0 and not neg_tf:
            keep.append(m)
        elif w_con > 0:
            tf_only.append((m, neg_tf))
        else:
            drop.append(m)
        print(f"{m.replace('KRW-',''):8s} {tot:6d}거래 가중exp 낙관 {w_opt:+.3%} / "
              f"보수 {w_con:+.3%}")
    print(f"\n유지: {', '.join(m.replace('KRW-','') for m in keep) or '없음'}")
    for m, tfs in tf_only:
        print(f"타임프레임 제한: {m.replace('KRW-','')} → "
              f"{', '.join(str(tfcore.TF_MINUTES[t]) + '분' for t in tfs)} 차단 "
              f"(charter.STRATEGY_BLACKLIST)")
    print(f"블랙리스트 후보: {', '.join(m.replace('KRW-','') for m in drop) or '없음'} "
          f"(charter.UNIVERSE_BLACKLIST)")
    (DATA_DIR / "universe_verdict.json").write_text(json.dumps(verdict, indent=2))
```

- [ ] **Step 4: 실행**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_universe.py`
Expected: 종목별 낙관/보수 exp 병기 표. **ATOM이 보수 스프레드에서 음수로 뒤집힐 가능성이 높다** — 그것이 이 태스크의 핵심 산출물이다. VANA·FIL·UNI·AAVE·PENDLE의 첫 판정도 나온다.

- [ ] **Step 5: 기존 테스트 회귀 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS. `test_timeframe_rules.py`는 헌장 상수를 보므로 영향 없어야 한다.

- [ ] **Step 6: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/lab_universe.py \
        upbit-rbi-bot/backtesting/research/data/universe_verdict.json
git commit -m "feat(research): 축4 미검증 종목 판정 + 낙관/보수 스프레드 병기"
```

---

### Task 8: 최종 통합 판정 (`lab_decide.py`)

**Files:**
- Create: `backtesting/research/lab_decide.py`

**Interfaces:**
- Consumes: `data/frontier_*.json`, `data/tfscan.json`, `data/universe_verdict.json`, `lab_tfcore.*`, `portfolio.run`, `fastsim.charter_pass`
- Produces: 표준출력 최종 판정 + `data/decision.json`

- [ ] **Step 1: 스크립트 작성**

```python
"""최종 통합 — 네 축의 생존 조합을 포트폴리오 수준에서 합쳐 판정한다.

왜 거래당 지표로 끝내지 않는가: 동시 3포지션 한도(§5.5) 때문이다. 신호가 3배로 늘어도
슬롯이 포화되면 실제 거래는 3배가 되지 않는다. 그래서 '신호 목록'이 아니라 '계좌 곡선'으로
비교한다.

판정: 사전 등록한 하한 3개(lab_tfcore.judge) + 헌장 §11 병기.
홀드아웃 조회 1회(예산의 마지막 1회).

실행: .venv/bin/python backtesting/research/lab_decide.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import charter as C
from backtesting.research import data_cache, lab_tfcore as tfcore, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import charter_pass, stats_chrono
from backtesting.research.lab_frontier import load_spreads
from backtesting.research.lab_final import SELECTION_START
from backtesting.research.lab_tfscan import source_for

CURRENT = [("minute5", 3.0, None), ("minute15", 3.0, None)]     # 라이브 v2.2 (비교 기준선)


def read(name: str, default):
    p = DATA_DIR / name
    return json.loads(p.read_text()) if p.exists() else default


def build_config() -> list[tuple[str, float, float | None]]:
    """축 2·3 결과에서 후보 구성을 만든다. (tf, th, gate) 목록."""
    tfscan = read("tfscan.json", [])
    alive_tfs = [r["tf"] for r in tfscan if r["exp_stress"] > 0 and r["exp"] > 0]
    cfg = []
    for tf in alive_tfs:
        rows = read(f"frontier_{tf}.json", [])
        ok = [r for r in rows if r["exp_stress"] > 0 and r["exp_cons"] > 0]
        if ok:
            best = max(ok, key=lambda r: r["daily_exp"])
            cfg.append((tf, best["th"], best["gate"]))
        else:
            cfg.append((tf, 3.0, None))       # 문턱은 현행 유지
    return cfg


def collect(cfg, markets, spreads, lo_hi: str) -> tuple:
    """구성 전체의 거래를 모은다. lo_hi='holdout'이면 홀드아웃 구간만."""
    items, pts, days = [], [], 0
    for tf, th, gate in cfg:
        p = tfcore.params_for(tf, gate=gate)
        for m in markets:
            df = source_for(m, tf)
            if df is None or len(df) < p.warmup + 500:
                continue
            split = int(df.index.searchsorted(SELECTION_START))
            end = split if lo_hi == "holdout" else len(df)
            if end <= p.warmup:
                continue
            days = max(days, (df.index[end - 1] - df.index[p.warmup]).days)
            i, t = tfcore.run_market(df, p, th, spreads.get(m, 0.001),
                                     start=p.warmup, end=end, market=m)
            items += i
            pts += t
    return stats_chrono(items), pts, days


def panel_of(tf, markets):
    out = {}
    for m in markets:
        df = source_for(m, tf)
        if df is not None:
            out[m] = df
    return out


def wf_of(cfg, markets, spreads):
    """구성 전체의 walk-forward OOS — 하한 ①의 정본 증거.

    타임프레임마다 폴드 OOS 거래를 모아 시간순으로 합친다(종목·전략을 이어붙이면 MDD가
    왜곡되므로 stats_chrono를 쓴다).
    """
    pts, days = [], 0
    for tf, th, gate in cfg:
        p = panel_of(tf, markets)
        if not p:
            continue
        g = gate if gate is not None else tfcore.params_for(tf).gate
        _, wpts, _, d = tfcore.walk_forward(p, tf, [(th, g)], spreads)
        pts += wpts
        days = max(days, d)
    items = [(t.entry_ts, t.pnl_ratio) for t in pts]
    return stats_chrono(items), pts, max(days, 1)


def report(name, cfg, markets, cons, n_compared):
    stress = {m: tfcore.STRESS_SPREAD for m in markets}
    s, pts, days = wf_of(cfg, markets, cons)
    if s.trades == 0:
        print(f"{name}: walk-forward OOS 거래 0건")
        return None
    ss, _, _ = wf_of(cfg, markets, stress)
    r = portfolio.run(pts)
    v = tfcore.judge(s, ss, r.mdd, n_compared)
    d_exp = tfcore.daily_exp(s, days)
    print(f"\n── {name} " + "─" * (54 - len(name)))
    print(f"구성: {', '.join(f'{tfcore.TF_MINUTES[t]}분(th={th:.0f}' + (f' gate={g:.2%})' if g else ')') for t, th, g in cfg)}")
    print(f"walk-forward OOS {s.trades}거래 / {days}일 = 하루 {s.trades/max(days,1):.2f}회")
    print(f"승률 {s.win_rate:.1%} PF {s.profit_factor:.2f} exp {s.exp:+.3%} "
          f"t{s.t_stat:+.2f} · 일일 기댓값 {d_exp:+.3%}")
    print(f"계좌 {r.total_return:+.1%} MDD {r.mdd:.1%} · {r.summary()}")
    print(f"비용 스트레스(왕복 0.25%) exp {ss.exp:+.3%}")
    print(f"하한 3개: {'✅ 통과' if v.passed else '❌ ' + ' / '.join(v.reasons)}")
    hs, hpts, hdays = collect(cfg, markets, cons, "holdout")
    if hs.trades:
        hr = portfolio.run(hpts)
        print(f"[확인용] 홀드아웃 {hs.trades}거래 exp {hs.exp:+.3%} t{hs.t_stat:+.2f} "
              f"계좌 {hr.total_return:+.1%} MDD {hr.mdd:.1%} · 하루 "
              f"{hs.trades/max(hdays,1):.2f}회")
    print(f"헌장 §11: {'✅ 통과' if charter_pass(s) else '❌ 불통과'} "
          f"(거래 {s.trades}≥{C.BACKTEST_MIN_TRADES} · 승률 {s.win_rate:.1%}"
          f"≥{C.BACKTEST_MIN_WINRATE:.0%} · PF {s.profit_factor:.2f}"
          f"≥{C.BACKTEST_MIN_PROFIT_FACTOR} · MDD {s.mdd:.1%}≤{C.BACKTEST_MAX_DRAWDOWN:.0%})")
    return {"name": name, "cfg": cfg, "trades": s.trades, "per_day": s.trades / max(days, 1),
            "exp": s.exp, "daily_exp": d_exp, "t": s.t_stat, "exp_stress": ss.exp,
            "mdd": r.mdd, "total_return": r.total_return, "passed": v.passed,
            "reasons": v.reasons, "charter_11": charter_pass(s)}


def main() -> None:
    opt, cons = load_spreads("opt"), load_spreads("cons")
    if not opt:
        print("스프레드 측정값 없음 — spread_check.py --sample2 먼저 실행")
        return
    uv = read("universe_verdict.json", {})
    # 보수 스프레드에서 가중 exp > 0 인 종목만 (축 4 판정 반영)
    ok_markets = []
    for m in sorted(opt):
        if opt[m] > C.MAX_SPREAD_RATIO:
            continue
        rows = uv.get(m)
        if rows is None:
            ok_markets.append(m)          # 축 4 미판정(대형) — 기존 검증 종목
            continue
        tot = sum(r["trades"] for r in rows.values())
        w = sum(r["exp_cons"] * r["trades"] for r in rows.values()) / max(tot, 1)
        if w > 0:
            ok_markets.append(m)
    print("최종 통합 판정 · 동시 3포지션·동일코인 금지·일일손실한도 반영")
    print(f"종목 {len(ok_markets)}개: {', '.join(m.replace('KRW-','') for m in ok_markets)}")
    print("홀드아웃 조회 1회 (예산 4회 중 마지막)\n")

    cand = build_config()
    n = 2 + len(cand)          # 비교한 구성 수(기준선 + 후보)
    results = [r for r in (report("기준선(라이브 v2.2)", CURRENT, ok_markets, cons, 1),
                           report("후보 구성", cand, ok_markets, cons, n)) if r]
    (DATA_DIR / "decision.json").write_text(json.dumps(results, indent=2))

    print("\n── 결론 " + "─" * 50)
    base = next((r for r in results if r["name"].startswith("기준선")), None)
    new = next((r for r in results if r["name"] == "후보 구성"), None)
    if not (base and new):
        print("비교 불가 — 위 출력 확인")
        return
    print(f"빈도  하루 {base['per_day']:.2f}회 → {new['per_day']:.2f}회 "
          f"({new['per_day']/max(base['per_day'],1e-9):.2f}배)")
    print(f"일일 기댓값  {base['daily_exp']:+.3%} → {new['daily_exp']:+.3%} "
          f"({new['daily_exp']-base['daily_exp']:+.3%}p)")
    if new["passed"] and new["daily_exp"] > base["daily_exp"]:
        print("✅ 후보 구성 채택 권고 — 헌장 개정안 작성으로 진행")
    else:
        print("❌ 후보 구성 기각 — 현행 유지가 정합적. 기준을 사후에 낮추지 않는다.")
        for r in new["reasons"]:
            print(f"   {r}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_decide.py`
Expected: 기준선과 후보 구성의 빈도·일일 기댓값 대조, 하한 3개 판정, §11 병기, 마지막에 채택/기각 결론.

- [ ] **Step 3: 결과가 기각이면 그대로 기록한다**

기각일 때 파라미터를 바꿔 재실행하는 것은 **금지**다. 그것이 홀드아웃을 태우는 행위이며, 이전 세션에서 th=10이 반전된 원인이다. 기각이면 Task 9에서 기각 사유를 문서화한다.

- [ ] **Step 4: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/lab_decide.py \
        upbit-rbi-bot/backtesting/research/data/decision.json
git commit -m "feat(research): 최종 통합 판정 — 포트폴리오 수준 하한 3개 + 헌장 §11 병기"
```

---

### Task 9: 결과 문서화와 헌장 개정안

**Files:**
- Modify: `backtesting/research/README.md`
- Modify: `docs/TRADING_CHARTER_KR.md`, `upbit-rbi-bot/config/charter.py` (**Task 8이 채택 결론일 때만**)
- Modify: `upbit-rbi-bot/tests/test_charter.py` (헌장 변경 시)

**Interfaces:**
- Consumes: `data/frontier_*.json`, `data/tfscan.json`, `data/universe_verdict.json`, `data/decision.json`

- [ ] **Step 1: README에 결과 절 추가**

`backtesting/research/README.md` 끝에 `## 거래 빈도 확대 검증 (2026-07-27)` 절을 추가한다. 반드시 포함할 항목:

- 스크립트 표에 `lab_tfcore.py` · `lab_frontier.py` · `lab_tfscan.py` · `lab_decide.py` 4행 추가
- 축 2 frontier 표(th × gate, 일일 기댓값 열 포함)와 "현행 대비 빈도 N배 / 일일 기댓값 Xp" 한 줄 결론
- 축 3 타임프레임 표(비용/움직임 비율 실측값 포함)와 1분봉 판정
- 축 4 종목별 낙관/보수 exp 표. **ATOM처럼 뒤집힌 종목은 별도로 강조**
- 사전 등록한 하한 3개와 실제 판정 결과
- **홀드아웃 조회 실제 횟수**(예산 4회 대비)
- **보수 스프레드의 ×2가 가정임을 명시**. `--watch` 24시간 실측이 나왔으면 실측값으로 대체하고 그 사실을 적는다
- 다중비교 보정 필요 t와 실제 t

- [ ] **Step 2: 24시간 스프레드 관측 결과 반영**

Task 4 Step 2의 `--watch`가 끝났으면 결과를 확인한다.

```bash
cd upbit-rbi-bot && .venv/bin/python - <<'PY'
import json, statistics
from backtesting.research.data_cache import DATA_DIR
raw = json.loads((DATA_DIR / "spreads_watch.json").read_text())
print(f"{'종목':8s} {'중앙':>9s} {'p75':>9s} {'최대':>9s} {'가정(중앙×2)':>13s}")
for m, v in sorted(raw.items()):
    s = sorted(v)
    med = statistics.median(s)
    p75 = s[int(len(s) * 0.75)]
    print(f"{m.replace('KRW-',''):8s} {med:9.3%} {p75:9.3%} {s[-1]:9.3%} {med*2:13.3%}")
PY
```

실측 p75가 가정값(×2)보다 크면 `spreads_cons.json`을 실측 p75로 갱신하고 **Task 8을 재실행**한다(비용이 커지는 방향은 기준을 느슨하게 하는 것이 아니므로 홀드아웃 재조회에 해당하지 않는다 — 다만 재조회 사실을 README에 기록한다). 실측이 가정보다 작으면 가정을 유지한다(보수적).

- [ ] **Step 3: 기각 결론일 때 — 여기서 끝낸다**

Task 8이 기각이면 헌장은 건드리지 않는다. README에 다음을 명시한다:

- 문턱 완화·1분봉·타임프레임 확장 각각의 기각 사유와 숫자
- "빈도는 늘 수 있지만 일일 기댓값이 개선되지 않는다"면 그 표를 근거로 제시
- 운영자에게 남는 선택지: (a) 현행 유지 (b) 하한 ②(비용 0.25% 생존)를 완화하고 리스크를 감수 (c) 신호 자체 재설계(이번 범위 밖)

Commit:
```bash
git add upbit-rbi-bot/backtesting/research/README.md
git commit -m "docs(research): 거래 빈도 확대 검증 결과 — 축별 판정과 기각 사유"
```

여기서 태스크를 종료한다. **Step 4~6은 채택 결론일 때만 수행한다.**

- [ ] **Step 4: 채택 결론일 때 — 헌장 상수 반영**

`config/charter.py`의 `STRATEGY_SPECS`에 채택된 타임프레임을 추가하고 파라미터를 갱신한다. 예시(30분봉이 채택된 경우):

```python
    "rsi2_30m": StrategySpec("rsi2_30m", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                             stop_pct=0.040, min_atr_ratio=0.0147, time_stop_bars=16,
                             timeframe="minute30"),
```

`CHARTER_VERSION`을 올린다(`"v2.3"`). 축 4에서 나온 블랙리스트·타임프레임 제한을 `UNIVERSE_BLACKLIST`·`STRATEGY_BLACKLIST`에 반영한다.

- [ ] **Step 5: 채택 결론일 때 — 라이브 코드 일치 확인 (배포 전 필수 관문)**

Run: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/lab_live_parity.py`
Expected: 불일치 0건.

새 타임프레임 전략을 추가했다면 그 전략도 parity 확인 대상에 넣어야 한다. `lab_live_parity.py`가 `rsi2`·`rsi2_15m`만 보고 있으면 새 전략을 추가하고 재실행한다. **불일치가 하나라도 있으면 배포하지 않는다** — 검증한 코드와 배포하는 코드가 다르다는 뜻이다.

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS. `test_charter.py`가 새 상수를 검증하도록 테스트를 추가한다.

- [ ] **Step 6: 채택 결론일 때 — 헌장 문서와 재승인 안내**

`docs/TRADING_CHARTER_KR.md`의 §2(전략 정의)·§13(파라미터 요약)을 갱신하고 개정 이력을 적는다.

Commit:
```bash
cd /Users/jasonair/Projects/life-change
git add docs/TRADING_CHARTER_KR.md upbit-rbi-bot/config/charter.py \
        upbit-rbi-bot/tests/test_charter.py upbit-rbi-bot/backtesting/research/README.md
git commit -m "feat: 헌장 v2.3 — 거래 빈도 확대 검증 결과 반영"
```

**운영자에게 반드시 알릴 것**: 헌장 개정 시 실거래를 유지하려면 서버 `.env`의
`LIVE_CHARTER_ACK`를 새 버전(`v2.3`)으로 갱신해야 한다(§9.7). 갱신하지 않으면 봇이
실거래를 멈춘다. 이 안내 없이 배포하면 운영자가 원인을 모르는 정지를 겪는다.

---

## 실행 순서 요약

```
Task 1  스프레드 도구 확장          (코드)
Task 2  청크 수집기                (코드 + 테스트)
Task 3  lab_tfcore 공통 코어        (코드 + 테스트 + parity)
Task 4  데이터 수집 실행            (API · 단독 · 약 2.5시간)
Task 5  축2 문턱 frontier
Task 6  축3 타임프레임 스캔 → 생존 TF에 축2 재실행
Task 7  축4 미검증 종목 판정
Task 8  최종 통합 판정
Task 9  문서화 (+ 채택 시 헌장 개정)
```

Task 4가 실패하거나 시간이 초과되면 **1분봉만 미판정으로 남기고 Task 5·6(1분봉 제외)·7·8을
진행한다** — 1분봉은 사전 예상이 기각이므로 다른 축을 막지 않는다.
