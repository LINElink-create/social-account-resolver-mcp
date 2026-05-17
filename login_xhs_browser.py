from __future__ import annotations

from tools.xiaohongshu import _browser_type, _new_browser_driver


def main() -> None:
    driver = _new_browser_driver()
    driver.get("https://www.xiaohongshu.com")

    browser = _browser_type()
    print(f"{browser} is open with the Xiaohongshu worker profile.")
    print("Log in to Xiaohongshu in that browser window, then press Enter here.")
    input()
    driver.quit()


if __name__ == "__main__":
    main()
