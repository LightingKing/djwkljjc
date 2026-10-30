from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"


class LocalSandboxIapSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = (CLIENT / "local-sandbox-iap.lua").read_text(encoding="utf-8")
        cls.entry = (CLIENT / "local-guest-entry.lua").read_text(encoding="utf-8")

    def test_adapter_is_runtime_only_and_has_both_local_routes(self) -> None:
        self.assertIn("fci/purchasetouch/", self.adapter)
        self.assertIn("fci/purchasedelevery/", self.adapter)
        self.assertIn("^http://127%.0%.0%.1:19080/?$", self.adapter)
        self.assertIn("return M", self.adapter)

    def test_adapter_requires_local_guest_before_intercepting(self) -> None:
        self.assertRegex(self.adapter, r"localGuest\(iap\)")
        self.assertRegex(
            self.adapter,
            re.compile(r"if not localGuest\(iap\) then\s+.*?return original", re.S),
        )
        self.assertIn("iap.__localSandboxPurchase == true", self.adapter)

    def test_delivery_only_uses_server_trade_token(self) -> None:
        # The delivery body is deliberately small and contains no platform
        # receipt/openid/access-token fields.  The server accepts only its
        # explicit local-sandbox token.
        delivery = re.search(r"local delivery = \{(?P<body>.*?)\n\s*\}", self.adapter, re.S)
        self.assertIsNotNone(delivery)
        body = delivery.group("body")
        self.assertIn("tradeData = tradeData", body)
        self.assertNotRegex(body, r"transactionReceipt|purchaseToken|openId|accessToken|receipt")
        self.assertIn("platform = 0", body)

    def test_entry_loads_adapter_without_making_it_required(self) -> None:
        self.assertIn("pcall(require,'local-sandbox-iap')", self.entry)
        self.assertIn("if sandboxIapOk and sandboxIap then", self.entry)
        # Loading the optional helper must not turn a missing runtime module
        # into a Login crash.
        self.assertIn("pcall(install,iap)", self.entry)

    def test_no_native_method_replacement_is_present(self) -> None:
        # This seam only wraps Lua IAPHandler.Purchase.  Native C# bindings
        # such as DataBindingManager:SetData are intentionally untouched.
        self.assertNotRegex(self.adapter, r"CS\.[A-Za-z0-9_.]+\.[A-Za-z0-9_]+\s*=")
        self.assertNotIn("m_iap:Purchase", self.adapter)
        self.assertNotIn("SyncMidas", self.adapter)


if __name__ == "__main__":
    unittest.main()
