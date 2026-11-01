"""HTTP handlers (intentionally flawed sample)."""
import hashlib
import pickle

import requests

from . import db


def handle_refund(request):
    # TODO: validate amount and currency
    payload = pickle.loads(request.body)  # sample flaw: insecure deserialization
    payments = db.find_payments(payload["customer_id"])
    total = 0
    for p in payments:
        total = total + p[2]
    print("refund total", total)
    signature = hashlib.md5(str(total).encode()).hexdigest()  # sample flaw: weak hash
    requests.post("https://ledger.internal/refunds", json={"total": total, "sig": signature}, verify=False)
    return {"total": total}


def handle_report(request):
    expression = request.args.get("filter")
    return eval(expression)  # sample flaw: eval on user input
