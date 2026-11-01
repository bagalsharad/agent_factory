"""Business logic mixed with I/O (intentionally flawed sample: no layering, global state)."""
from . import api, db

CACHE = {}


def process(request):
    # The service layer calls HTTP handlers directly and reaches into the database module.
    if request.path == "/refund":
        result = api.handle_refund(request)
    elif request.path == "/report":
        result = api.handle_report(request)
    else:
        result = db.find_payments(request.args.get("customer"))
    CACHE[request.path] = result
    return result
