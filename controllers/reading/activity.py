"""Reading statistics aggregation for the CrossPoint Sync API.

Pure functions (no I/O) ported from crosspoint-sync `models/stats.ts` and
`models/activity.ts`, so the results match what the CrossPoint Sync app expects.
"""
import base64
import datetime
import math

FINISHED_PERCENTAGE = 0.98
HISTORY_BYTES = 92
HISTORY_DAYS = 730


def js_round(value: float) -> int:
    """Round half up like JavaScript's Math.round (Python's round() is banker's rounding)."""
    return math.floor(value + 0.5)


def utc_date_key(timestamp: int | float) -> str:
    """Return the UTC calendar day ('YYYY-MM-DD') of a unix timestamp."""
    return datetime.datetime.fromtimestamp(timestamp, datetime.UTC).strftime('%Y-%m-%d')


def lenient_b64decode(value: str) -> bytes:
    """Decode base64 the way Node's Buffer.from(value, 'base64') does: never fail."""
    cleaned = ''.join(ch for ch in value if ch.isalnum() or ch in '+/-_')
    cleaned = cleaned.replace('-', '+').replace('_', '/')
    if len(cleaned) % 4 == 1:
        # A lone trailing character carries no full byte: Node drops it
        cleaned = cleaned[:-1]
    cleaned += '=' * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned)
    except ValueError:
        return b''


def _get_bit(buffer: bytes, bit: int) -> bool:
    byte = bit >> 3
    return byte < len(buffer) and bool(buffer[byte] & (1 << (bit & 7)))


def _set_bit(buffer: bytearray, bit: int) -> None:
    byte = bit >> 3
    if byte < len(buffer):
        buffer[byte] |= 1 << (bit & 7)


def combine_history(snapshots: list[dict]) -> tuple[int, bytes]:
    """Re-anchor every device's reading-day bitmap on the newest anchor day and OR them."""
    combined = bytearray(HISTORY_BYTES)
    with_bits = [s for s in snapshots if s.get('anchor_day', 0) > 0]
    if not with_bits:
        return 0, bytes(combined)
    anchor_day = max(s['anchor_day'] for s in with_bits)
    for snapshot in with_bits:
        shift = anchor_day - snapshot['anchor_day']
        bits = lenient_b64decode(snapshot.get('history_b64', ''))
        for n in range(0, HISTORY_DAYS - shift):
            if _get_bit(bits, n):
                _set_bit(combined, n + shift)
    return anchor_day, bytes(combined)


def longest_streak(history: bytes) -> int:
    """Longest run of consecutive reading days in a history bitmap."""
    best = run = 0
    for n in range(HISTORY_DAYS):
        if _get_bit(history, n):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def current_streak(history: bytes) -> int:
    """Current streak: counted from today, or from yesterday when today is not read yet."""
    n = 0 if _get_bit(history, 0) else 1
    streak = 0
    while n < HISTORY_DAYS and _get_bit(history, n):
        streak += 1
        n += 1
    return streak


def summarize_global_stats(devices: list[dict], daily: list[dict]) -> dict:
    """Combine the per-device global snapshots into the /stats/summary response."""
    snapshots = [d['stats'] for d in devices]
    anchor_day, history = combine_history(snapshots)
    streak = max([longest_streak(history)] + [s.get('streak', 0) for s in snapshots] + [0])
    return {
        'sessions': sum(s.get('sessions', 0) for s in snapshots),
        'seconds': sum(s.get('seconds', 0) for s in snapshots),
        'pages': sum(s.get('pages', 0) for s in snapshots),
        'completed': sum(s.get('completed', 0) for s in snapshots),
        'tod': [sum(s['tod'][i] for s in snapshots) for i in range(4)],
        'dow': [sum(s['dow'][i] for s in snapshots) for i in range(7)],
        'anchor_day': anchor_day,
        'history_b64': base64.b64encode(history).decode('ascii'),
        'streak': streak,
        'current_streak': current_streak(history),
        'daily': [{'date': d['date'], 'seconds': d['seconds'], 'minutes': d['seconds'] / 60} for d in daily],
        'devices': [
            {'device_id': d['device_id'], 'device': d['device'], 'updated_at': d['updated_at']}
            for d in devices
        ],
    }


def combine_book_stats(snapshots: list[dict]) -> dict:
    """Combine per-device book snapshots (sums, OR of completed, weighted average pace)."""
    paced = [s for s in snapshots if s.get('pace_n', 0) > 0]
    pace_total = sum(s['pace_n'] for s in paced)
    avg_fwd = js_round(sum(s['avg_fwd'] * s['pace_n'] for s in paced) / pace_total) if pace_total else 0
    starts = [s['start_date'] for s in snapshots if s.get('start_date', 0) > 0]
    return {
        'sessions': sum(s.get('sessions', 0) for s in snapshots),
        'seconds': sum(s.get('seconds', 0) for s in snapshots),
        'pages': sum(s.get('pages', 0) for s in snapshots),
        'completed': any(s.get('completed') for s in snapshots),
        'avg_fwd': avg_fwd,
        'start_date': min(starts) if starts else 0,
        'finished_date': max([s.get('finished_date', 0) for s in snapshots] + [0]),
        'tod': [sum(s['tod'][i] for s in snapshots) for i in range(4)],
        'dow': [sum(s['dow'][i] for s in snapshots) for i in range(7)],
    }


def compute_activity(
    log_rows: list[dict],
    documents: dict[str, dict],
    finished_dates: dict[str, int],
    start_dates: dict[str, int],
    tz: int,
) -> dict:
    """Derive reading days, pages and finished books from the progress history.

    Args:
        log_rows: progress_log rows ({document, percentage, at}) for every device.
        documents: per-document info ({page_count, status, status_at, start_date, finished_date}).
        finished_dates / start_dates: dates reported by the devices' book statistics.
        tz: client's Date#getTimezoneOffset() in minutes (minutes behind UTC).
    """
    def local_noon(date_seconds: int | None) -> int | None:
        return date_seconds + 12 * 3600 + tz * 60 if date_seconds else None

    def day_key(at: int) -> str:
        return utc_date_key(at - tz * 60)

    days: dict[str, dict] = {}
    reading_days: set[str] = set()

    def day_entry(key: str) -> dict:
        return days.setdefault(key, {'pages': 0.0, 'syncs': 0, 'books': {}})

    def register_sync(at: int, document: str, start: float) -> tuple[dict, dict]:
        key = day_key(at)
        reading_days.add(key)
        day = day_entry(key)
        book = day['books'].setdefault(document, {'document': document, 'pages': 0.0, 'syncs': 0, 'from': start, 'to': start})
        day['syncs'] += 1
        book['syncs'] += 1
        return day, book

    by_document: dict[str, list[dict]] = {}
    for row in log_rows:
        by_document.setdefault(row['document'], []).append(row)

    books = []
    pages_total = 0
    for document, entries in by_document.items():
        entries.sort(key=lambda r: r['at'])
        info = documents.get(document, {})
        page_count = info.get('page_count') or None

        furthest = entries[0]['percentage']
        log_finish = entries[0]['at'] if furthest >= FINISHED_PERCENTAGE else None
        register_sync(entries[0]['at'], document, furthest)
        for row in entries[1:]:
            day, book = register_sync(row['at'], document, furthest)
            if row['percentage'] <= furthest:
                continue
            if page_count:
                gained = (row['percentage'] - furthest) * page_count
                day['pages'] += gained
                book['pages'] += gained
            furthest = row['percentage']
            book['to'] = furthest
            if log_finish is None and furthest >= FINISHED_PERCENTAGE:
                log_finish = row['at']

        manual_finish = local_noon(info.get('finished_date'))
        dated_finish = manual_finish if manual_finish is not None else local_noon(finished_dates.get(document))
        status = info.get('status')
        if status is None:
            finished = dated_finish if dated_finish is not None else log_finish
        elif status == 'finished':
            finished = next((v for v in (dated_finish, log_finish, info.get('status_at')) if v is not None), None)
        else:
            finished = None

        if dated_finish is not None and finished == dated_finish:
            key = day_key(dated_finish)
            day = day_entry(key)
            if document not in day['books']:
                reached = max([r['percentage'] for r in entries if day_key(r['at']) <= key] + [0])
                day['books'][document] = {'document': document, 'pages': 0.0, 'syncs': 0, 'from': reached, 'to': reached}

        pages_read = js_round(furthest * page_count) if page_count else None
        pages_total += pages_read or 0

        manual_start = local_noon(info.get('start_date'))
        dated_start = manual_start if manual_start is not None else local_noon(start_dates.get(document))
        started = dated_start if dated_start is not None else entries[0]['at']
        if dated_start is not None:
            key = day_key(dated_start)
            day = day_entry(key)
            book = day['books'].setdefault(document, {'document': document, 'pages': 0.0, 'syncs': 0, 'from': 0, 'to': 0})
            book['started'] = True
            reading_days.add(key)
        if dated_finish is not None and finished == dated_finish:
            reading_days.add(day_key(dated_finish))

        real_finish = finished is not None and (finished == dated_finish or finished == log_finish)
        days_to_finish = None
        if real_finish and (dated_start is not None or finished > started):
            span = (datetime.date.fromisoformat(day_key(finished)) - datetime.date.fromisoformat(day_key(started))).days + 1
            if span >= 1:
                days_to_finish = span

        books.append({
            'document': document,
            'started_at': started,
            'last_at': entries[-1]['at'],
            'percentage': furthest,
            'finished_at': finished,
            'page_count': page_count,
            'pages_read': pages_read,
            'days_to_finish': days_to_finish,
            'start_manual': manual_start is not None,
            'finish_manual': manual_finish is not None and finished == manual_finish,
        })

    # Books marked as read without any progress (web UI): finished on the date set by hand,
    # else the device's, else when the status was set; the whole book counts as read
    for document, info in documents.items():
        if document in by_document or info.get('status') != 'finished':
            continue
        manual_finish = local_noon(info.get('finished_date'))
        dated_finish = manual_finish if manual_finish is not None else local_noon(finished_dates.get(document))
        finished = dated_finish if dated_finish is not None else info.get('status_at')
        if not finished:
            continue
        if dated_finish is not None:
            key = day_key(dated_finish)
            day_entry(key)['books'].setdefault(document, {'document': document, 'pages': 0.0, 'syncs': 0, 'from': 0, 'to': 0})
            reading_days.add(key)
        manual_start = local_noon(info.get('start_date'))
        dated_start = manual_start if manual_start is not None else local_noon(start_dates.get(document))
        if dated_start is not None:
            key = day_key(dated_start)
            book = day_entry(key)['books'].setdefault(document, {'document': document, 'pages': 0.0, 'syncs': 0, 'from': 0, 'to': 0})
            book['started'] = True
            reading_days.add(key)
        days_to_finish = None
        if dated_finish is not None and dated_start is not None:
            span = (datetime.date.fromisoformat(day_key(dated_finish)) - datetime.date.fromisoformat(day_key(dated_start))).days + 1
            if span >= 1:
                days_to_finish = span
        page_count = info.get('page_count') or None
        pages_total += page_count or 0
        books.append({
            'document': document,
            'started_at': dated_start if dated_start is not None else finished,
            'last_at': finished,
            'percentage': 1.0,
            'finished_at': finished,
            'page_count': page_count,
            'pages_read': page_count,
            'days_to_finish': days_to_finish,
            'start_manual': manual_start is not None,
            'finish_manual': manual_finish is not None,
        })

    books.sort(key=lambda b: b['last_at'], reverse=True)
    return {
        'pages_total': pages_total,
        'books': books,
        'reading_days': sorted(reading_days),
        'days': [
            {
                'day': key,
                'pages': js_round(day['pages']),
                'syncs': day['syncs'],
                'books': [{**book, 'pages': js_round(book['pages'])} for book in day['books'].values()],
            }
            for key, day in sorted(days.items())
        ],
    }
