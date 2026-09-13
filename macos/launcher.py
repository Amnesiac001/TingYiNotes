import os
import sys

from classnote.config import application_data_dir


if getattr(sys, "frozen", False):
    app_data = application_data_dir()
    app_data.mkdir(parents=True, exist_ok=True)
    os.chdir(app_data)

from classnote.qt_gui import main  # noqa: E402


if __name__ == "__main__":
    main()
