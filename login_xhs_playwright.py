from __future__ import annotations

from app.services.xhs_browser import get_browser


def main() -> None:
    browser = get_browser()
    page = browser._new_page()
    page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded")
    print("Playwright Chromium is open with the configured Xiaohongshu profile.")
    print("Log in with QR code or phone in that browser window, then press Enter here.")
    input()
    page.close()
    browser.close()


if __name__ == "__main__":
    main()

