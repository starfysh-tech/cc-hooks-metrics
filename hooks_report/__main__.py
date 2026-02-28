import os
import sys
from . import config
from .cli import parse_args
from .db import HooksDB


def main():
    args = parse_args()
    db_path = args.db or os.environ.get("CLAUDE_HOOKS_DB") or config.DEFAULT_DB_PATH
    db = HooksDB(db_path)

    if args.export:
        from .static import export_json
        export_json(db)
    elif args.static or not sys.stdout.isatty():
        from .static import render_static
        render_static(db, verbose=args.verbose)
    else:
        from .tui import HooksReportApp
        HooksReportApp(db).run()


if __name__ == "__main__":
    main()
