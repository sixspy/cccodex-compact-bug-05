from app.handlers.base import BaseHandler
from app.services.thr_stale_ledger import append_ledger, dashboard
from app.services.thr_stale_surface import reconcile_surface, snapshot


class ApiThrStaleDash(BaseHandler):
    def get(self):
        self.require_user()
        self.json(dashboard())


class ApiThrStaleNote(BaseHandler):
    def post(self):
        self.require_user()
        body = self.read_json()
        self.json(
            append_ledger(
                int(body.get("plan_id") or 0),
                str(body.get("note") or "阈值旧闸"),
            )
        )


class ApiThrStaleHalf(BaseHandler):
    def post(self):
        self.require_user()
        self.json(reconcile_surface())


class ApiThrStaleSnap(BaseHandler):
    def get(self):
        self.require_user()
        self.json(snapshot())
