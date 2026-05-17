from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")


def main() -> None:
    user_data_dir = os.getenv(
        "XHS_EDGE_USER_DATA_DIR",
        r"D:\MCP\SearchCoser\edge-xhs-profile",
    )
    profile = os.getenv("XHS_EDGE_PROFILE", "Default")
    driver_path = os.getenv("XHS_EDGE_DRIVER_PATH", "").strip()

    options = EdgeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(f"--profile-directory={profile}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    service = EdgeService(executable_path=driver_path) if driver_path else EdgeService()
    driver = webdriver.Edge(service=service, options=options)
    driver.get("https://www.xiaohongshu.com")

    print("Edge is open with the Xiaohongshu worker profile.")
    print("Log in to Xiaohongshu in that browser window, then press Enter here.")
    input()
    driver.quit()


if __name__ == "__main__":
    main()
