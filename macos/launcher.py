import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    application_support = Path.home() / "Library" / "Application Support"
    preferred = application_support / "听译记"
    legacy = application_support / "ClassNote"
    app_data = legacy if legacy.exists() and not preferred.exists() else preferred
    app_data.mkdir(parents=True, exist_ok=True)
    os.chdir(app_data)

from classnote.qt_gui import main  # noqa: E402


if __name__ == "__main__":
    main()
