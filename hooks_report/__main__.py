import json
import os
import sys
from . import config
from .cli import parse_args
from .db import HooksDB, HooksDBError


def main():
    args = parse_args()
    if args.include_sensitive and not args.export_spans:
        print("warn: --include-sensitive has no effect without --export-spans", file=sys.stderr)
    db_path = args.db or os.environ.get("CLAUDE_HOOKS_DB") or config.DEFAULT_DB_PATH
    db = HooksDB(db_path)

    try:
        if args.export_spans:
            from .spans import hook_metric_to_span, audit_event_to_span, spans_to_dict  # type: ignore
            hook_rows = db.spans_raw()
            audit_rows = db.audit_spans_raw()
            redact = not args.include_sensitive
            spans = []
            skip_count = 0
            for r in hook_rows:
                try:
                    spans.append(hook_metric_to_span(r, redact=redact))
                except (ValueError, IndexError, TypeError) as e:
                    skip_count += 1
                    print(f"warn: skipped hook row {r[0]}: {e}", file=sys.stderr)
            for r in audit_rows:
                try:
                    spans.append(audit_event_to_span(r, redact=redact))
                except (ValueError, IndexError, TypeError) as e:
                    skip_count += 1
                    print(f"warn: skipped audit row {r[0]}: {e}", file=sys.stderr)
            if skip_count:
                print(f"warn: {skip_count} row(s) skipped due to conversion errors", file=sys.stderr)
            spans.sort(key=lambda s: s.start_time_unix_nano)
            print(json.dumps(spans_to_dict(spans), indent=2))
        elif args.export:
            from .static import export_json
            export_json(db)
        elif args.static or not sys.stdout.isatty():
            from .static import render_static
            render_static(db, verbose=args.verbose)
        else:
            from .tui import HooksReportApp
            HooksReportApp(db).run()
    except HooksDBError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
