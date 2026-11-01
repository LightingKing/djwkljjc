"""Local sandbox mall (商城) for the retired 零境交错 client.

The client's Purchase window has three visible tabs, each fed differently
(decompiled Lua under ``analysis/lua-decompiled``):

* 迷子充值 (``window/purchase/purchasevip``): the product list is the client
  table ``PurchaseInfo`` filtered by platform/showInShop
  (``manager__iaphandler.lua`` Start/GetProducts).  The list is only sized on
  the ``OnGetPurchaseActivityInfo`` event, which the stock client raises from
  the Tencent Midas SDK callback; buying calls the Midas SDK between
  ``fci/purchasetouch/`` and ``fci/purchasedelevery/``.  Both need the client
  Lua patch described by :data:`IAPHANDLER_PATCH_FUNCTIONS`.
* 等级礼包 (``window/purchase/purchaselvgift``): ``Mall`` rows with
  ``giftPage == 1``.  The list is only built inside the ``GET fci/mall-info/``
  callback, which iterates ``response.mallRecord.summary`` and
  ``response.purchaseRecord.buyRecord`` with ``pairs`` -- an empty typed map
  decodes to nil there, so the reply is plain camelCase JSON with explicit
  (possibly empty) objects.
* 活动礼包 (``window/purchase/purchaseactivitygift``): ``Mall`` rows with
  ``giftPage == 2``; a row is listed only while ``ActivityList`` holds an open
  ``ActivityType_Mall`` (15) activity with the row's ``activityId``.
  :meth:`LocalMall.activity_rows` supplies those activity records.

Buying a gift pack (``POST fci/mall-trade``) spends in-game currency and
grants the table reward; buying a ``PurchaseInfo`` product is a sandbox
order: ``purchasetouch`` records a pending order and returns a one-time
``tradeData`` token, ``purchasedelevery`` redeems the token and credits the
product immediately.  No real payment provider is contacted.
"""
from __future__ import annotations

from server.core.reset import DEFAULT_RESET_POLICY, ResetPolicy, policy_for

import json
import copy
import hashlib
import re
import secrets
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory

try:
    from .grants import grant_rewards
    from . import equipment
    from . import local_mailbox
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from grants import grant_rewards
    import equipment
    import local_mailbox


Body = bytes | bytearray | str | None
Response = tuple[int, Any]

TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"

RES_PLAYER_MONEY = 5        # 迷子 total (bound + paid), fci/resource/ResPlayerMoney_0
RES_PLAYER_PAY_MONEY = 27   # paid part of the total, fci/resource/ResPlayerPayMoney_0
RES_EQUIP = 12
RES_ACTOR_SKIN = 29
RES_MONTH_CARD_DAYS = 24
ACTIVITY_TYPE_MALL = 15
ACTIVITY_TYPE_DIRECT_PURCHASE = 37
DIRECT_PURCHASE_ACTIVITY_ID = 37001

GIFT_PAGE_LEVEL = 1
GIFT_PAGE_ACTIVITY = 2
GIFT_PAGE_DIRECT = 3

# MallBuyIntervalType
INTERVAL_DAY, INTERVAL_WEEK, INTERVAL_MONTH = 1, 2, 3
RESET_HOUR_UTC = 6          # same daily reset as tasks.py
_DAY = 86400

# Mall activities are already running (start in the past, no preheat, no end)
# so the client neither shows a preheat tag nor an expiry countdown.
MALL_ACTIVITY_START = 1704067200  # 2024-01-01T00:00:00Z

# Full delivery replies include every resource delta and used to grow the
# per-account JSON state forever. Keep a bounded recent reply cache while a
# compact token marker preserves idempotent retries after a reply is trimmed.
MAX_DELIVERED_REPLIES = 128
MAX_DELIVERED_TOKENS = 1024
# Local, unpaid sandbox checkout attempts expire after a day. Keep at most
# 64 active attempts per account; their audit orders and token hashes remain.
MAX_PENDING_ORDERS = 64
PENDING_ORDER_TTL = _DAY


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _route(path: str) -> str:
    return (urlsplit(path or "").path or "/").rstrip("/").lower()


def _rows(name: str, table_dir: Path = TABLE_DIR) -> list[dict[str, Any]]:
    try:
        payload = json.loads((table_dir / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


def _resource(value: Mapping[str, Any]) -> dict[str, int]:
    return {"type": _int(value.get("type")), "id": _int(value.get("id")),
            "count": _int(value.get("count")), "param": _int(value.get("param"))}


def window_start(interval: int, now: int, policy: ResetPolicy = DEFAULT_RESET_POLICY) -> int:
    """Start of the purchase-limit window containing ``now`` (0 = never resets)."""

    if interval == INTERVAL_DAY:
        return policy.period_start(now)
    if interval == INTERVAL_WEEK:
        return policy.period_start(now, "week")
    if interval == INTERVAL_MONTH:
        return policy.period_start(now, "month")
    return 0


class MallCatalog:
    """The client tables behind the mall, loaded once."""

    def __init__(self, table_dir: Path = TABLE_DIR) -> None:
        self.mall: dict[int, dict[str, Any]] = {}
        for row in _rows("Mall", table_dir):
            if _int(row.get("id")) > 0:
                self.mall[_int(row["id"])] = row
        self.products: dict[str, dict[str, Any]] = {}
        for row in _rows("PurchaseInfo", table_dir):
            product_id = str(row.get("productId") or "")
            if product_id:
                self.products[product_id] = row
        self.delay_rewards: dict[int, list[dict[str, Any]]] = {
            _int(row.get("activityId")): [r for r in row.get("delayReward") or [] if isinstance(r, Mapping)]
            for row in _rows("DelayReward", table_dir)
        }
        self.skin_actors: dict[int, list[int]] = {
            _int(row.get("id")): [_int(a) for a in row.get("actorIds") or []]
            for row in _rows("ActorSkin", table_dir)
        }
        # Original APK xlsx__misc.lua:48-51 explicitly includes daily gold.
        misc = next(iter(_rows("Misc", table_dir)), {})
        self.month_card_daily_rewards = [_resource(row) for row in misc.get("monthCardRewardDaily", [])]

    def page(self, gift_page: int) -> list[dict[str, Any]]:
        """``PB.all("Mall"):where(isMonth == false and giftPage == page)``."""

        return [row for _, row in sorted(self.mall.items())
                if not row.get("isMonth") and _int(row.get("giftPage")) == gift_page]

    def shop_products(self, platform: int) -> list[dict[str, Any]]:
        """IAPHandler.GetProducts: platform rows with showInShop, by price."""

        rows = [row for row in self.products.values()
                if _int(row.get("platform")) == platform and row.get("showInShop")]
        return sorted(rows, key=lambda row: float(row.get("price") or 0))

    def mall_row_for_product(self, product_id: str, *,
                             open_activity_ids: Optional[set[int]] = None) -> Optional[dict[str, Any]]:
        rows = [row for _, row in sorted(self.mall.items())
                if str(row.get("productId") or "") == product_id]
        if open_activity_ids is not None:
            # Re-runs share a product id, but only the current Mall activity is
            # visible. Keep a closed fallback so callers still reject it.
            rows.sort(key=lambda row: (bool(_int(row.get("activityId")))
                                       and _int(row["activityId"]) not in open_activity_ids, -_int(row["id"])))
        return rows[0] if rows else None

    def activity_gift_rows(self, *, dedupe: bool = True, include_direct: bool = False) -> list[dict[str, Any]]:
        """Event gift rows whose activities the local server keeps open.

        The 104 giftPage-2 rows are 40 distinct packs re-run under new
        activity ids; with ``dedupe`` only the newest id of each identical
        pack (price, currency, contents, limits, delayed rewards) is opened.
        """

        pages = [GIFT_PAGE_ACTIVITY] + ([GIFT_PAGE_DIRECT] if include_direct else [])
        rows = [row for page in pages for row in self.page(page) if _int(row.get("activityId"))]
        if not dedupe:
            return rows
        newest: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = json.dumps([
                _int(row.get("giftPage")), str(row.get("productId") or ""), _int(row.get("price")),
                _int(row.get("resourceType")), [_resource(r) for r in row.get("reward") or []],
                _int(row.get("maxBuyCount")), _int(row.get("maxCountClearInterval")),
                _int(row.get("limitLv")), bool(row.get("isDelay")), _int(row.get("sustainedDays")),
                [_resource(r) for r in self.delay_rewards.get(_int(row.get("activityId")), [])],
            ], sort_keys=True)
            if key not in newest or _int(row["id"]) > _int(newest[key]["id"]):
                newest[key] = row
        return sorted(newest.values(), key=lambda row: _int(row["id"]))


_CATALOG: Optional[MallCatalog] = None


def catalog() -> MallCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = MallCatalog()
    return _CATALOG


class MallError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message

    def reply(self) -> Response:
        body = {"error": self.code}
        if self.message:
            body["message"] = self.message
        return self.status, body


class LocalMall:
    """Persistent sandbox mall: records, orders, limits and deliveries."""

    def __init__(
        self,
        game: Any,
        *,
        clock: Optional[Callable[[], int]] = None,
        mall_catalog: Optional[MallCatalog] = None,
        dedupe_activity_gifts: bool = True,
        include_direct_purchase: bool = True,
    ) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or getattr(game, "_now", None) or (lambda: int(time.time()))
        self.catalog = mall_catalog or catalog()
        self.include_direct_purchase = include_direct_purchase
        self.activity_gifts = self.catalog.activity_gift_rows(
            dedupe=dedupe_activity_gifts, include_direct=include_direct_purchase)
        self.open_activity_ids = {_int(row["activityId"]) for row in self.activity_gifts}
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_mall_state (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_mall_orders (
                    order_id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    product_id TEXT NOT NULL DEFAULT '',
                    mall_id INTEGER NOT NULL DEFAULT 0,
                    price REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    delivered_at INTEGER,
                    res_change_json TEXT
                )
                """
            )
            connection.execute("CREATE TABLE IF NOT EXISTS local_mall_receipts ("
                               "account_id INTEGER NOT NULL, token_sha256 TEXT NOT NULL, "
                               "transaction_id TEXT NOT NULL, product_id TEXT NOT NULL, "
                               "channel INTEGER NOT NULL, status TEXT NOT NULL, "
                               "recorded_at INTEGER NOT NULL, "
                               "PRIMARY KEY(account_id, token_sha256))")
            # Persist actual legacy token evidence before trimming any JSON
            # cache. Order IDs alone cannot prove possession of an old token.
            for row in connection.execute("SELECT account_id, state_json FROM local_mall_state").fetchall():
                try:
                    state = json.loads(row["state_json"])
                except (ValueError, TypeError):
                    continue
                if not isinstance(state, dict):
                    continue
                for key, limit in (("delivered", MAX_DELIVERED_REPLIES),
                                   ("deliveredTokens", MAX_DELIVERED_TOKENS)):
                    entries = state.get(key)
                    if not isinstance(entries, dict):
                        continue
                    for token, reply in entries.items():
                        if isinstance(reply, Mapping) and str(token).startswith("local-sandbox:"):
                            self._index_receipt(connection, row["account_id"], token, reply)
                    while len(entries) > limit:
                        entries.pop(next(iter(entries)))
                encoded = json.dumps(state, separators=(",", ":"))
                if encoded != row["state_json"]:
                    connection.execute("UPDATE local_mall_state SET state_json=? WHERE account_id=?",
                                       (encoded, row["account_id"]))

    # ------------------------------------------------------------- plumbing
    def _now(self) -> int:
        return int(self._clock())

    def _index_receipt(self, connection, account_id, token, reply, status="delivered"):
        connection.execute(
            "INSERT INTO local_mall_receipts VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id, token_sha256) DO NOTHING",
            (int(account_id), hashlib.sha256(token.encode("utf-8")).hexdigest(),
             str(reply.get("transactionId", reply.get("orderId", ""))),
             str(reply.get("productId", "")), _int(reply.get("channel")), status, self._now()))

    def _receipt(self, profile, token):
        with closing(self.game._connect()) as connection:
            row = connection.execute(
                "SELECT transaction_id, product_id, channel, status FROM local_mall_receipts "
                "WHERE account_id=? AND token_sha256=?",
                (int(profile["account_id"]), hashlib.sha256(token.encode("utf-8")).hexdigest())).fetchone()
        return dict(row) if row is not None else None

    def _prune_pending(self, profile, state, *, reserve=0):
        pending = state["pending"]
        expired = [token for token, order in pending.items()
                   if not isinstance(order, Mapping)
                   or self._now() - _int(order.get("createdAt")) >= PENDING_ORDER_TTL]
        remaining = sorted((token for token in pending if token not in expired),
                           key=lambda token: _int(pending[token].get("createdAt")))
        excess = max(0, len(remaining) - max(0, MAX_PENDING_ORDERS - reserve))
        cancelled = remaining[:excess]
        if not expired and not cancelled:
            return
        with closing(self.game._connect()) as connection, connection:
            for token in expired + cancelled:
                order = pending.pop(token)
                if not isinstance(order, Mapping):
                    order = {}
                status = "expired" if token in expired else "superseded"
                self._index_receipt(connection, profile["account_id"], token, order, status)
                connection.execute("UPDATE local_mall_orders SET status=? "
                                   "WHERE order_id=? AND account_id=? AND status='pending'",
                                   (status, str(order.get("orderId", "")), int(profile["account_id"])))
        self._save(profile, state)

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection:
            row = connection.execute(
                "SELECT state_json FROM local_mall_state WHERE account_id = ?", (account_id,)).fetchone()
        try:
            state = json.loads(row["state_json"]) if row is not None else {}
        except (TypeError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        for key in ("mall", "purchase", "pending", "firstDouble", "monthCard", "delivered", "deliveredTokens"):
            if not isinstance(state.get(key), dict):
                state[key] = {}
        return state

    @contextmanager
    def _atomic(self, profile: Mapping[str, Any]):
        """Rollback both durable state and the request's in-memory snapshot."""
        saved = copy.deepcopy(dict(profile))
        try:
            with self.game.atomic_mutation():
                # A request may have waited behind another transaction.
                with closing(self.game._connect()) as connection:
                    row = connection.execute("SELECT resources_json FROM game_profiles WHERE account_id = ?",
                                             (int(profile["account_id"]),)).fetchone()
                if row is not None and isinstance(profile, dict):
                    profile["resources"] = json.loads(row["resources_json"] or "[]")
                yield
        except Exception:
            if isinstance(profile, dict):
                profile.clear()
                profile.update(saved)
            raise

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO local_mall_state(account_id, state_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (int(profile["account_id"]), json.dumps(state, separators=(",", ":")), self._now()),
            )

    def _order(self, order_id: str, profile: Mapping[str, Any], *, kind: str, status: str,
               product_id: str = "", mall_id: int = 0, price: float = 0.0, currency: str = "",
               res_change: Optional[list] = None) -> None:
        now = self._now()
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO local_mall_orders(order_id, account_id, kind, product_id, mall_id, price, currency, "
                "status, created_at, delivered_at, res_change_json) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(order_id) DO UPDATE SET status = excluded.status, delivered_at = excluded.delivered_at, "
                "res_change_json = excluded.res_change_json",
                (order_id, int(profile["account_id"]), kind, product_id, int(mall_id), float(price), currency,
                 status, now, now if status == "delivered" else None,
                 json.dumps(res_change, ensure_ascii=False, separators=(",", ":")) if res_change is not None else None),
            )

    def orders(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        with self._lock, closing(self.game._connect()) as connection:
            rows = connection.execute(
                "SELECT order_id, kind, product_id, mall_id, price, currency, status, created_at, delivered_at "
                "FROM local_mall_orders WHERE account_id = ? ORDER BY created_at, order_id",
                (int(profile["account_id"]),)).fetchall()
        return [dict(row) for row in rows]

    def _request(self, body: Body, type_name: str) -> dict[str, Any]:
        """Decode a JSON body or the client's native wireformat request."""

        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                return value
        except (UnicodeDecodeError, ValueError):
            pass
        try:
            message = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(type_name))()
            message.ParseFromString(raw)
            return json_format.MessageToDict(message)
        except Exception:
            return {}

    def _effective(self, record: Any, interval: int, now: int) -> int:
        if not isinstance(record, Mapping):
            return 0
        count = _int(record.get("buyCount"))
        if interval in (INTERVAL_DAY, INTERVAL_WEEK, INTERVAL_MONTH) and \
                _int(record.get("lastBuyStartTime")) < window_start(interval, now, policy_for(self.game)):
            return 0
        return count

    def _bump(self, records: dict[str, Any], key: str, interval: int) -> int:
        now = self._now()
        current = self._effective(records.get(key), interval, now)
        previous = records.get(key) if current else None
        start = _int(previous.get("lastBuyStartTime")) if isinstance(previous, Mapping) else now
        records[key] = {"buyCount": current + 1, "lastBuyStartTime": start or now}
        return current + 1

    @staticmethod
    def _balance(profile: Mapping[str, Any], resource_type: int) -> int:
        return sum(_int(row.get("count")) for row in profile.get("resources") or []
                   if isinstance(row, Mapping) and _int(row.get("type")) == resource_type
                   and _int(row.get("id")) == 0 and _int(row.get("param")) == 0)

    @staticmethod
    def _base_res(delta: Mapping[str, Any]) -> dict[str, Any]:
        return {"baseRes": {"type": _int(delta.get("type")), "id": _int(delta.get("id")),
                            "param": _int(delta.get("param")), "count": _int(delta.get("count")),
                            "countDelta": _int(delta.get("countDelta"))}, "inBox": []}

    # ------------------------------------------------------------ economics
    def _spend(self, profile: Mapping[str, Any], resource_type: int, price: int) -> list[dict[str, Any]]:
        """Charge a gift pack; 迷子 follow WU.IsEnoughMoney (bound first)."""

        if price <= 0 or resource_type <= 0:
            return []
        changes = [{"type": resource_type, "id": 0, "param": 0, "count": -price}]
        ok, deltas, error = self.game.economy._change_resources(profile, changes)
        if not ok:
            raise MallError(409, error or "insufficient_resource")
        return [self._base_res(delta) for delta in deltas]

    def _grant(self, profile: Mapping[str, Any], rewards: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Grant STResource rows; paid 迷子 also raise the 迷子 total."""

        plain: list[dict[str, int]] = []
        equips: list[int] = []
        skins: list[int] = []
        for reward in rewards:
            value = _resource(reward)
            if value["type"] <= 0 or value["count"] <= 0:
                continue
            if value["type"] == RES_EQUIP and value["id"] in equipment.EQUIP_INFO:
                equips.extend([value["id"]] * value["count"])
                continue
            plain.append(value)
            if value["type"] == RES_ACTOR_SKIN:
                skins.append(value["id"])
        changes: list[dict[str, Any]] = []
        # Propagate ledger failures instead of accepting a partially delivered
        # product (grant_rewards deliberately tolerates absent stores elsewhere).
        ledger = [r for r in plain if r["type"] not in (11, 15)]
        if ledger:
            ok, deltas, error = self.game.economy._change_resources(profile, ledger)
            if not ok:
                raise MallError(409, error or "resource_update_failed")
            changes.extend(self._base_res(delta) for delta in deltas)
        for change in grant_rewards(self.game, profile, [r for r in plain if r["type"] in (11, 15)]):
            if "baseRes" in change:
                change.setdefault("inBox", [])
            if isinstance(change.get("actor"), dict) and not _int(change["actor"].get("countDelta")):
                change["actor"]["countDelta"] = 1  # WU.ShowRewards only lists countDelta > 0
            changes.append(change)
        for equip_id in equips:
            changes.append(equipment.grant_equipment(self.game, profile, equip_id))
        if skins and getattr(self.game, "systems", None) is not None:
            state = self.game.systems._state(profile)
            unlocked = state.setdefault("unlockedSkins", {})
            for skin_id in skins:
                for actor_id in self.catalog.skin_actors.get(skin_id, []):
                    owned = unlocked.setdefault(str(actor_id), [])
                    if skin_id not in owned:
                        owned.append(skin_id)
            self.game.systems._save(profile, state)
        return changes

    # ---------------------------------------------------------------- views
    def mall_info(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """``GET fci/mall-info/`` as the camelCase object the client reads."""

        state, now = self._state(profile), self._now()
        player_id = str(profile.get("player_id", ""))
        summary: dict[str, Any] = {}
        for key, record in state["mall"].items():
            row = self.catalog.mall.get(_int(key))
            interval = _int(row.get("maxCountClearInterval")) if row else 0
            if isinstance(record, Mapping):
                summary[str(_int(key))] = {"buyCount": self._effective(record, interval, now),
                                           "lastBuyStartTime": _int(record.get("lastBuyStartTime"))}
        buy_record: dict[str, Any] = {}
        for product_id, record in state["purchase"].items():
            if isinstance(record, Mapping):
                buy_record[str(product_id)] = {
                    "buyCount": self._effective(record, self._product_interval(str(product_id)), now),
                    "lastBuyStartTime": _int(record.get("lastBuyStartTime"))}
        return {"mallRecord": {"playerId": player_id, "summary": summary},
                "purchaseRecord": {"playerId": player_id, "buyRecord": buy_record}}

    def redpoint_fields(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """``fci/redpoint/data.mallInfoResult`` (RestifizerManager stores it as fci/mall-info)."""

        return {"mallInfoResult": self.mall_info(profile)}

    def activity_rows(self) -> list[dict[str, Any]]:
        """Open ``ActivityType_Mall`` records for the event gift tab (ActivityList)."""

        rows = []
        for activity_id in sorted(self.open_activity_ids):
            rows.append({
                "type": ACTIVITY_TYPE_MALL, "activityId": activity_id,
                "timePreheat": "local", "timeStart": "local", "timeEnd": "local",
                # timestampPreheat must be a number: the client compares it
                # unconditionally; 0 = no preheat.  timestampEnd 0 = no end.
                "timestampPreheat": 0, "timestampStart": MALL_ACTIVITY_START, "timestampEnd": 0,
                "forever": True, "category": 0, "weekdays": [], "timeClose": "", "timestampClose": 0,
                "dropFloorChannel": 0, "levelLimit": 0, "dayOpenTime": [], "season": 0,
                "weekdayPreheart": 0, "timeType": 0, "versionLimit": "", "preOpenTime": 0,
            })
        if self.include_direct_purchase:
            # The tab has its own activity type, while individual goods still
            # refer to their original per-product Mall activity IDs.
            trigger = dict(rows[0]) if rows else {
                "timestampPreheat": 0, "timestampStart": MALL_ACTIVITY_START,
                "timestampEnd": 0, "forever": True, "weekdays": [], "dayOpenTime": [],
            }
            trigger.update(type=ACTIVITY_TYPE_DIRECT_PURCHASE, activityId=DIRECT_PURCHASE_ACTIVITY_ID)
            rows.append(trigger)
        return rows

    @staticmethod
    def _tier_key(product: Mapping[str, Any]) -> str | None:
        if (product.get("showInShop") and _int(product.get("count")) > 0
                and not any(_int(r.get("type")) for r in product.get("purchaseRes") or [])):
            return str(_int(product["count"]))
        return None

    def purchase_activity_info(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Midas' activity callback shape, keyed by purchased currency amount."""
        state = self._state(profile)
        tiers = {}
        for product in self.catalog.products.values():
            key = self._tier_key(product)
            if key is not None:
                tiers.setdefault(key, product)
        ranks, ranges = [], []
        for key in sorted(tiers, key=int):
            first = not bool(state["firstDouble"].get(key))
            bonus = int(key) if first else _int(tiers[key].get("promotionCnt"))
            ranks.append({"num": key, "send_ext": "1" if first else ""})
            ranges.append({"num": key, "coin": [{"send_num": str(bonus)}]})
        return {"mp_info": {"utp_mpinfo_detail": {"rank_info": ranks, "range_info": ranges}}}

    def _month_mail(self, profile: Mapping[str, Any], day: int) -> int:
        """Persist the daily mail inside the purchase/settlement transaction."""
        state = self.game.mailbox._load(profile)
        mail_id = _int(state.get("mail_last_id")) + 1
        now = self._now()
        record = {"id": mail_id, "title": "月卡每日奖励", "content": "今日月卡奖励已送达。",
                  "senderName": "月卡福利", "attachments": copy.deepcopy(self.catalog.month_card_daily_rewards),
                  "read": False, "attachmentsGot": False, "createTime": now,
                  "startTime": now, "expireTime": now + 30 * _DAY, "important": True,
                  "monthCardDay": day}
        state["mail_last_id"] = mail_id
        state["mails"].append(record)
        trimmed = []
        if len(state["mails"]) > local_mailbox.MAX_MAILS:
            state["mails"].sort(key=lambda mail: _int(mail.get("id")))
            excess = len(state["mails"]) - local_mailbox.MAX_MAILS
            trimmed = [_int(mail["id"]) for mail in state["mails"][:excess]]
            del state["mails"][:excess]
        self.game.mailbox._save(profile, state)
        payload = {"mails": [local_mailbox.mail_view(record)], "clearAll": False, "delMails": trimmed}
        def notify():
            notifier = getattr(self.game.mailbox, "notifier", None)
            if notifier is not None:
                notifier(profile, payload)
        self.game.after_commit(notify)
        return mail_id

    def _settle_month_card(self, profile: Mapping[str, Any], state: dict[str, Any], *, purchase: bool = False) -> list[dict[str, Any]]:
        """Expire missed days and issue at most today's single mail.

        Type 24 is the number of unconsumed days after today's settlement.
        The separate reward marker prevents same-day renewals from consuming
        another day or sending another mail, even after a restart.
        """
        remaining = self._balance(profile, RES_MONTH_CARD_DAYS)
        record = state["monthCard"]
        if not remaining and not record:
            return []
        today = policy_for(self.game).day_index(self._now())
        last_day = _int(record.get("lastDay"), today - 1 if remaining else today)
        if today < last_day:
            return []  # never replay days after a clock rollback
        elapsed = max(0, today - last_day)
        reward_due = remaining > 0 and elapsed > 0 and remaining >= elapsed
        consumed = min(remaining, elapsed)
        if purchase and remaining > consumed and record.get("lastRewardDay") != today and elapsed == 0:
            consumed += 1
            reward_due = True
        changes = []
        if consumed:
            ok, deltas, error = self.game.economy._change_resources(profile, [
                {"type": RES_MONTH_CARD_DAYS, "id": 0, "count": -consumed}])
            if not ok:
                raise MallError(409, error or "month_card_settlement_failed")
            changes = [self._base_res(delta) for delta in deltas]
        if reward_due and record.get("lastRewardDay") != today:
            record["lastMailId"] = self._month_mail(profile, today)
            record["lastRewardDay"] = today
        record["lastDay"] = today
        return changes

    def settle_month_card(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Call on authenticated requests/login and from the daily scheduler."""
        with self._atomic(profile):
            state = self._state(profile)
            old = copy.deepcopy(state["monthCard"])
            changes = self._settle_month_card(profile, state)
            if state["monthCard"] != old:
                self._save(profile, state)
            if changes:
                def notify():
                    notifier = getattr(self.game, "base_push_notifier", None)
                    if notifier is not None:
                        notifier(profile, 1)
                self.game.after_commit(notify)
            return changes

    def merge_activity_list(self, method: str, path: str, result: Optional[Response]) -> Optional[Response]:
        """Append :meth:`activity_rows` to a ``GET fci/activity/`` list reply."""

        if (method.upper() != "GET" or _route(path) != "/fci/activity" or not isinstance(result, tuple)
                or len(result) != 2 or result[0] != 200 or not isinstance(result[1], list)):
            return result
        present = {(_int(row.get("type")), _int(row.get("activityId")))
                   for row in result[1] if isinstance(row, Mapping)}
        extra = [row for row in self.activity_rows() if (row["type"], row["activityId"]) not in present]
        return 200, list(result[1]) + extra

    def _product_interval(self, product_id: str) -> int:
        product = self.catalog.products.get(product_id) or {}
        interval = _int(product.get("maxCountClearInterval"))
        if not interval:
            row = self.catalog.mall_row_for_product(product_id, open_activity_ids=self.open_activity_ids)
            interval = _int(row.get("maxCountClearInterval")) if row else 0
        return interval

    def _product_limit(self, product_id: str) -> int:
        product = self.catalog.products.get(product_id) or {}
        limit = _int(product.get("maxBuyCount"))
        if not limit:
            row = self.catalog.mall_row_for_product(product_id, open_activity_ids=self.open_activity_ids)
            limit = _int(row.get("maxBuyCount")) if row else 0
        return limit

    # -------------------------------------------------------------- actions
    def mall_trade(self, profile: Mapping[str, Any], body: Body) -> Optional[Response]:
        """``POST fci/mall-trade`` {id}: buy a gift pack with in-game currency.

        Ids outside the client ``Mall`` table return None (left to economy.py).
        """

        mall_id = _int(self._request(body, "ApiMallTrade").get("id"))
        row = self.catalog.mall.get(mall_id)
        if row is None:
            return None
        if row.get("isMonth"):
            raise MallError(404, "mall_good_not_found")
        if str(row.get("productId") or ""):
            raise MallError(400, "mall_good_requires_purchase", "buy this pack through fci/purchasetouch/")
        activity_id = _int(row.get("activityId"))
        if activity_id and activity_id not in self.open_activity_ids:
            raise MallError(409, "mall_activity_closed")
        if _int(profile.get("level"), 1) < _int(row.get("limitLv")):
            raise MallError(409, "player_level_too_low", f"requires level {_int(row.get('limitLv'))}")
        state = self._state(profile)
        interval, limit = _int(row.get("maxCountClearInterval")), _int(row.get("maxBuyCount"))
        if limit and self._effective(state["mall"].get(str(mall_id)), interval, self._now()) >= limit:
            raise MallError(409, "mall_good_limit_reached")
        res_change = self._spend(profile, _int(row.get("resourceType")), _int(row.get("price")))
        rewards = list(row.get("reward") or [])
        if row.get("isDelay"):
            # Sandbox: the daily delayed rewards (DelayReward, normally mailed
            # over sustainedDays) are granted up front with the pack.
            days = max(1, _int(row.get("sustainedDays"), 1))
            rewards += list(self.catalog.delay_rewards.get(activity_id, [])) * days
        res_change += self._grant(profile, rewards)
        buy_count = self._bump(state["mall"], str(mall_id), interval)
        self._save(profile, state)
        self._order(f"mall-{secrets.token_hex(8)}", profile, kind="mall", status="delivered", mall_id=mall_id,
                    price=float(_int(row.get("price"))), currency=f"res:{_int(row.get('resourceType'))}",
                    res_change=res_change)
        return 200, {"resChange": res_change, "buyCount": buy_count}

    def purchase_touch(self, profile: Mapping[str, Any], body: Body) -> Optional[Response]:
        """``POST fci/purchasetouch/``: open a sandbox order for a PurchaseInfo product.

        Product ids outside ``PurchaseInfo`` return None (economy.py's
        ``local.sandbox.*`` seam keeps handling those).
        """

        request = self._request(body, "ApiPurchaseTouchProduct")
        product_id = str(request.get("productId") or request.get("product_id") or "")
        product = self.catalog.products.get(product_id)
        if product is None:
            return None
        row = self.catalog.mall_row_for_product(product_id, open_activity_ids=self.open_activity_ids)
        if row is not None and _int(row.get('activityId')) and _int(row['activityId']) not in self.open_activity_ids:
            raise MallError(409, 'mall_activity_closed')
        if row is not None and _int(profile.get("level"), 1) < _int(row.get("limitLv")):
            raise MallError(409, "player_level_too_low")
        state = self._state(profile)
        limit = self._product_limit(product_id)
        if limit and self._effective(state["purchase"].get(product_id), self._product_interval(product_id),
                                     self._now()) >= limit:
            raise MallError(409, "purchase_limit_reached")
        self._prune_pending(profile, state, reserve=1)
        token = "local-sandbox:" + secrets.token_urlsafe(18)
        order_id = "iap-" + secrets.token_hex(8)
        # ``count`` from the client is the product's 迷子 amount (presetCount),
        # not a quantity: one order always delivers one product.
        state["pending"][token] = {"productId": product_id, "orderId": order_id, "createdAt": self._now(),
                                   "channel": _int(request.get("channel"))}
        self._save(profile, state)
        self._order(order_id, profile, kind="iap", status="pending", product_id=product_id,
                    price=float(product.get("price") or 0), currency="CNY_SANDBOX")
        return 200, {"tradeData": token, "isSandbox": True, "productId": product_id, "orderId": order_id}

    def _delivery_contents(self, product: Mapping[str, Any], *, first_double: bool = False) -> list[dict[str, Any]]:
        goods = [r for r in product.get("purchaseRes") or [] if isinstance(r, Mapping) and _int(r.get("type"))]
        if goods:
            return goods
        count, bonus = _int(product.get("count")), _int(product.get("promotionCnt"))
        if first_double:
            bonus = count
        contents = [{"type": RES_PLAYER_PAY_MONEY, "id": 0, "count": count}]  # also raises the total
        if bonus:
            contents.append({"type": RES_PLAYER_MONEY, "id": 0, "count": bonus})  # bound bonus 迷子
        return contents

    @staticmethod
    def _delivery_marker(reply: Mapping[str, Any]) -> dict[str, Any]:
        """The small durable subset needed to answer an old-token retry."""

        return {
            "transactionId": str(reply.get("transactionId", "")),
            "productId": str(reply.get("productId", "")),
            "channel": _int(reply.get("channel")),
        }

    def _remember_delivery(self, state: dict[str, Any], token: str, reply: Mapping[str, Any], *, profile=None) -> None:
        """Store a bounded full reply plus a compact idempotency marker."""

        if profile is not None:
            with closing(self.game._connect()) as connection, connection:
                self._index_receipt(connection, profile["account_id"], token, reply)
        delivered = state.setdefault("delivered", {})
        markers = state.setdefault("deliveredTokens", {})
        delivered[token] = dict(reply)
        markers[token] = self._delivery_marker(reply)
        while len(delivered) > MAX_DELIVERED_REPLIES:
            delivered.pop(next(iter(delivered)), None)
        while len(markers) > MAX_DELIVERED_TOKENS:
            markers.pop(next(iter(markers)), None)

    def purchase_delivery(self, profile: Mapping[str, Any], body: Body) -> Optional[Response]:
        """``POST fci/purchasedelevery/``: redeem the sandbox token and credit the product.

        Tokens this mall did not issue return None (economy.py answers them,
        including its 402 for a missing/forged token).
        """

        request = self._request(body, "ApiPurchaseDelevery")
        state = self._state(profile)
        self._prune_pending(profile, state)
        token = ""
        for key in ("tradeData", "trade_data", "transactionReceipt", "transactionId", "orderId"):
            candidate = str(request.get(key) or "")
            if candidate.startswith("local-sandbox:"):
                token = candidate
                break
        if token and token not in state["pending"]:
            done = state.get("delivered", {}).get(token)
            if isinstance(done, Mapping):  # retried delivery: no second credit
                reply = dict(done)
                reply['resChange'] = []
                reply['buyCount'] = self._effective(state['purchase'].get(str(done.get('productId'))),
                                                    self._product_interval(str(done.get('productId'))), self._now())
                return 200, reply
            marker = state.get("deliveredTokens", {}).get(token)
            if not isinstance(marker, Mapping):
                receipt = self._receipt(profile, token)
                if receipt is not None:
                    if receipt["status"] != "delivered":
                        return 409, {"error": "purchase_token_expired"}
                    marker = {"transactionId": receipt["transaction_id"],
                              "productId": receipt["product_id"], "channel": receipt["channel"]}
            if isinstance(marker, Mapping):
                # The full response was evicted, but the token marker still
                # proves this order was delivered. Return a replay-safe
                # summary and never credit the product a second time.
                product_id = str(marker.get("productId", ""))
                return 200, {
                    "transactionId": str(marker.get("transactionId", token.removeprefix("local-sandbox:"))),
                    "googlePurchaseToken": "",
                    "channel": _int(request.get("channel"), _int(marker.get("channel"))),
                    "productId": product_id,
                    "buyCount": self._effective(state["purchase"].get(product_id), self._product_interval(product_id), self._now()),
                    "resChange": [],
                    "sandbox": True,
                }
            return None
        if not token:
            return None
        pending = state["pending"].pop(token)
        product_id = str(pending.get("productId") or "")
        product = self.catalog.products.get(product_id)
        if product is None:
            raise MallError(409, "pending_product_unavailable")
        requested_product = str(request.get("productId") or "")
        if requested_product and requested_product != product_id:
            raise MallError(409, "purchase_product_mismatch")
        limit = self._product_limit(product_id)
        if limit and self._effective(state["purchase"].get(product_id), self._product_interval(product_id), self._now()) >= limit:
            raise MallError(409, "purchase_limit_reached")
        row = self.catalog.mall_row_for_product(product_id, open_activity_ids=self.open_activity_ids)
        if row is not None and _int(row.get('activityId')) and _int(row['activityId']) not in self.open_activity_ids:
            raise MallError(409, 'mall_activity_closed')
        if row is not None and _int(profile.get("level"), 1) < _int(row.get("limitLv")):
            raise MallError(409, "player_level_too_low")
        tier = self._tier_key(product)
        first_double = tier is not None and not state["firstDouble"].get(tier)
        # Older successful orders deliberately do not consume the new offer.
        res_change = self._settle_month_card(profile, state)
        res_change += self._grant(profile, self._delivery_contents(product, first_double=first_double))
        if any(_int(r.get("type")) == RES_MONTH_CARD_DAYS for r in product.get("purchaseRes") or []):
            res_change += self._settle_month_card(profile, state, purchase=True)
        if first_double:
            state["firstDouble"][tier] = str(pending.get("orderId") or token)
        buy_count = self._bump(state["purchase"], product_id, self._product_interval(product_id))
        reply = {
            "transactionId": str(pending.get("orderId") or token.removeprefix("local-sandbox:")),
            "googlePurchaseToken": "",
            "channel": _int(request.get("channel"), _int(pending.get("channel"))),
            "productId": product_id, "buyCount": buy_count, "resChange": res_change, "sandbox": True,
        }
        self._remember_delivery(state, token, reply, profile=profile)
        self._save(profile, state)
        self._order(str(pending.get("orderId") or token), profile, kind="iap", status="delivered",
                    product_id=product_id, price=float(product.get("price") or 0), currency="CNY_SANDBOX",
                    res_change=res_change)
        def notify_purchase():
            notifier = getattr(self.game, 'base_push_notifier', None)
            if notifier is not None:
                notifier(profile, 61, (product_id, buy_count))
                notifier(profile, 1)
        self.game.after_commit(notify_purchase)
        return 200, reply

    # ------------------------------------------------------------- dispatch
    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route, method = _route(path), method.upper()
        handlers = {
            ("GET", "/fci/mall-info"): lambda: (200, self.mall_info(profile)),
            ("GET", "/fci/purchase-activity-info"): lambda: (200, self.purchase_activity_info(profile)),
            ("POST", "/fci/mall-trade"): lambda: self.mall_trade(profile, body),
            ("POST", "/fci/purchasetouch"): lambda: self.purchase_touch(profile, body),
            ("POST", "/fci/purchasedelevery"): lambda: self.purchase_delivery(profile, body),
            # IAPHandler.SyncMidas: no Midas account exists locally.
            ("PATCH", "/fci/syncmidas"): lambda: (200, {}),
            ("POST", "/fci/syncmidas"): lambda: (200, {}),
        }
        handler = handlers.get((method, route))
        if handler is None:
            if route in {path for _, path in handlers}:
                return 405, {"error": "method_not_allowed"}
            return None
        try:
            with self._atomic(profile):
                return handler()
        except MallError as error:
            return error.reply()


# ---------------------------------------------------------------------------
# Client Lua patch for Manager/IAPHandler (spec; built and delivered by the
# hot-update channel, runtime/build_lua_bundle.py + runtime/deliver_patch.py).
# Each entry replaces the top-level function of that name in
# analysis/lua-decompiled/manager__iaphandler.lua; OnLocalDelivered and
# LocalPurchaseFailed are appended.
IAPHANDLER_PATCH_FUNCTIONS: dict[str, str] = {
    # Prices: always the local "Purchase_LocalPrice" strings from PurchaseInfo
    # (the remote branch waits for the Midas/App Store SDK).
    "UseRemoteProduct": """function UseRemoteProduct()
  return false
end
""",
    "Refresh": """function Refresh()
  ResolveProductPrice()
end
""",
    "GetLocalizedPrice": """function GetLocalizedPrice(productId)
  if m_localPrice == nil and m_products ~= nil then
    ResolveProductPrice()
  end
  if m_localPrice ~= nil then
    return m_localPrice[productId]
  end
end
""",
    # The recharge list (PurchaseVip) is sized only on this event, which the
    # stock client raises from the Midas SDK activity callback.
    # Deferred like the stock Midas callback: PurchaseVip.OnEnable calls this
    # before Start(), which resets the list to 0 rows, so an immediate
    # broadcast would be wiped.
    "GetActivityInfo": """function GetActivityInfo()
  this:DelayInvokeInSeconds(0.1, function()
    this:GameRequest("fci/purchase-activity-info/"):Get(function(result)
      OnGetActivityInfo(JsonE(result or {}))
    end, function()
      OnGetActivityInfo("{}")
    end)
  end)
end
""",
    # No MSDK login locally: WGGetLoginRecord() fails and the stock proxy
    # resets the game (it also wraps every 迷子 spend via WU.TryToPay).
    "MidasProxy": """function MidasProxy(callback)
  if callback ~= nil then
    callback()
  end
end
""",
    # PushService.OnScLoginBegin calls this with m_midasInfoCache == nil
    # (IAPHandler.bytes:407).  The huge TssReportInterval keeps the TSS MTP
    # report loop started right after this call parked, as it was while the
    # nil-index error aborted OnScLoginBegin here.
    "SyncMidas": """function SyncMidas(callback)
  if m_midasInfoCache == nil then
    this:SetData("TssReportInterval", 1000000000)
  end
  if callback ~= nil then
    callback()
  end
end
""",
    # Sandbox order: purchasetouch -> purchasedelevery, no m_iap:Purchase.
    "PurchaseImpl": """function PurchaseImpl(product, channel, count)
  m_channel = channel
  if m_timeout ~= nil then
    this:StopCoroutine(m_timeout)
  end
  m_timeout = this:DelayInvokeInSeconds(30, function()
    m_timeout = nil
    LocalPurchaseFailed()
  end)
  WU.SetLockFlag(WU.LockInputFlag.Purchase, true)
  local accountCache = this:GetData("accountCache") or {}
  this:GameRequest("fci/purchasetouch/"):Post({
    isGuest = accountCache.entryType == "guest",
    productId = product.productId,
    platform = m_platform,
    channel = m_channel,
    count = count
  }, function(response)
    if response == nil or response.tradeData == nil or response.tradeData == "" then
      LocalPurchaseFailed()
      return
    end
    this:GameRequest("fci/purchasedelevery/"):Post({
      productId = product.productId,
      tradeData = response.tradeData,
      platform = m_platform,
      channel = m_channel
    }, function(result)
      OnLocalDelivered(product, result)
    end, LocalPurchaseFailed)
  end, LocalPurchaseFailed)
end
""",
}

IAPHANDLER_APPENDED_FUNCTIONS = """
function LocalPurchaseFailed()
  WU.ShowHintText(WU.GetString("Purchase_PurchaseFailed"))
  FinishPurchase()
end

function OnLocalDelivered(product, result)
  if result ~= nil and result.resChange ~= nil then
    require("Manager/DataBindingHandler").ResChange(result.resChange)
  end
  DB:HandleRemoteChanged("fci/baseresource/")
  GetActivityInfo()
  if result ~= nil and result.buyCount ~= nil then
    this:BroadcastGameEvent("PushTypePurchaseBuyRecord", {
      product.productId,
      tostring(result.buyCount)
    })
  end
  if IsGoods(product) then
    this:BroadcastGameEvent("HandlePurchaseGoodsNew", product.productId)
  elseif result ~= nil and result.resChange ~= nil then
    WU.ShowRewards(result.resChange)
  end
  FinishPurchase()
end
"""


def build_iaphandler_patch(source: str) -> str:
    """Apply :data:`IAPHANDLER_PATCH_FUNCTIONS` to the decompiled module source."""

    source = source.replace("\r\n", "\n").rstrip("\n") + "\n"
    for name, replacement in IAPHANDLER_PATCH_FUNCTIONS.items():
        pattern = re.compile(rf"^function {re.escape(name)}\(.*?^end\n", re.M | re.S)
        source, count = pattern.subn(lambda _match: replacement, source, count=1)
        if count != 1:
            raise ValueError(f"function {name} not found in IAPHandler source")
    return source.rstrip("\n") + "\n" + IAPHANDLER_APPENDED_FUNCTIONS


__all__ = [
    "LocalMall", "MallCatalog", "MallError", "catalog", "window_start",
    "IAPHANDLER_PATCH_FUNCTIONS", "IAPHANDLER_APPENDED_FUNCTIONS", "build_iaphandler_patch",
]
