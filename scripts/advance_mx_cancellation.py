"""Review or advance one approved, already-stamped Mexico replacement pair or chain."""
import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from business_central_client.client import BusinessCentralClient
from business_central_client.config import Settings
from business_central_client.mx_cancellation import MxReplacement, advance_mx_cancellation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Submit once or refresh an existing cancellation")
    parser.add_argument("--finalize-accounting", action="store_true", help="Also reverse accounting after fresh SAT confirmation")
    args = parser.parse_args()
    if args.finalize_accounting and not args.apply:
        parser.error("--finalize-accounting requires --apply")
    data = json.loads(args.plan.read_text())
    data["amount_including_vat"] = Decimal(str(data["amount_including_vat"]))
    data["replacement_due_date"] = date.fromisoformat(data["replacement_due_date"])
    for key in ("original_amount_including_vat", "final_amount_including_vat"):
        if data.get(key) is not None:
            data[key] = Decimal(str(data[key]))
    if data.get("final_due_date") is not None:
        data["final_due_date"] = date.fromisoformat(data["final_due_date"])
    plan = MxReplacement(**data)
    plan.payload()
    load_dotenv()
    result = advance_mx_cancellation(
        BusinessCentralClient(Settings.from_env()), plan,
        apply=args.apply, finalize_accounting=args.finalize_accounting,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
