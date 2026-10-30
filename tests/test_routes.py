from pathlib import Path
import tempfile
import unittest

from server.core.routes import RouteContract, RouteContractError, RouteRegistry, RouteResponse


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "api.yaml"


class RouteRegistryTests(unittest.TestCase):
    def test_loads_descriptor_contracts_and_reports_unimplemented_routes(self):
        registry = RouteRegistry.from_yaml(CONTRACTS)
        self.assertEqual(len(registry.contracts), 84)
        response = registry.dispatch("POST", "/Dir/")
        self.assertEqual(response.status, 501)
        self.assertEqual(response.payload["error"], "route_not_implemented")
        self.assertEqual(response.payload["interface"], "ReqDir")
        report = registry.coverage_report()
        self.assertEqual(report["totalInterfaces"], 84)
        self.assertEqual(report["implementedInterfaces"], 0)
        self.assertEqual(report["unimplementedCalls"], {"ReqDir": 1})

    def test_registers_dynamic_route_and_extracts_parameters(self):
        registry = RouteRegistry.from_yaml(CONTRACTS)
        observed = []

        def handler(request, params):
            observed.append((request, dict(params)))
            return RouteResponse(201, {"app": params["app_id"]})

        registry.register("ApiAppById", handler)
        response = registry.dispatch("POST", "/App/demo-app/", {"ok": True})
        self.assertEqual(response.status, 201)
        self.assertEqual(response.payload, {"app": "demo-app"})
        self.assertEqual(observed, [({"ok": True}, {"app_id": "demo-app"})])
        report = registry.coverage_report()
        self.assertEqual(report["implementedInterfaces"], 1)
        app_status = next(item["status"] for item in report["interfaces"] if item["interface"] == "ApiAppById")
        self.assertEqual(app_status, "implemented")

    def test_method_and_unknown_route_are_separate(self):
        registry = RouteRegistry.from_yaml(CONTRACTS)
        registry.register("ReqDir", lambda _request, _params: {"ok": True}, method="GET")
        self.assertEqual(registry.dispatch("POST", "/Dir").status, 501)
        self.assertEqual(registry.dispatch("GET", "/Dir").status, 200)
        unknown = registry.dispatch("GET", "/does-not-exist")
        self.assertEqual(unknown.status, 404)
        self.assertEqual(registry.coverage_report()["notFoundCalls"], 1)

    def test_duplicate_registration_is_rejected_unless_replaced(self):
        registry = RouteRegistry([RouteContract("Example", "/example/")])
        registry.register("Example", lambda _request, _params: 1)
        with self.assertRaises(RouteContractError):
            registry.register("Example", lambda _request, _params: 2)
        registry.register("Example", lambda _request, _params: 3, replace=True)
        self.assertEqual(registry.dispatch("POST", "/example/").payload, 3)


if __name__ == "__main__":
    unittest.main()


