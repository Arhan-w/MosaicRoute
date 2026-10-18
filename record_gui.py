import os
import time
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

os.environ["MOSAIC_ADMIN_PASSWORD"] = "MosaicRouteSecurePass2026!"
os.environ["MOSAIC_LOCAL_API_KEY"] = "mosaic_local_test_key_123456789"
os.environ["MOSAIC_PORT"] = "8000"

media_dir = Path("media/images")
media_dir.mkdir(parents=True, exist_ok=True)
video_dir = Path("media/videos")
video_dir.mkdir(parents=True, exist_ok=True)

import uvicorn
from app import app

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()
time.sleep(2)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(video_dir),
        record_video_size={"width": 1440, "height": 900}
    )
    page = context.new_page()
    
    print("Navigating to login...")
    page.goto("http://127.0.0.1:8000")
    page.wait_for_selector("#login-screen")
    page.screenshot(path=str(media_dir / "login.png"))
    
    print("Logging in...")
    page.fill("#password", "MosaicRouteSecurePass2026!")
    page.click("#login-form button[type='submit']")
    
    page.wait_for_selector("#app-shell")
    time.sleep(1.5)
    
    print("Capturing Overview...")
    page.screenshot(path=str(media_dir / "dashboard.png"))
    
    print("Capturing Providers...")
    page.click("button[data-page='providers']")
    time.sleep(1)
    page.screenshot(path=str(media_dir / "providers.png"))
    
    print("Capturing Models & Aliases...")
    page.click("button[data-page='models']")
    time.sleep(1)
    page.screenshot(path=str(media_dir / "models.png"))
    
    print("Capturing Playground...")
    page.click("button[data-page='playground']")
    time.sleep(1)
    page.screenshot(path=str(media_dir / "playground.png"))
    
    print("Capturing Request Logs...")
    page.click("button[data-page='logs']")
    time.sleep(1)
    page.screenshot(path=str(media_dir / "logs.png"))
    
    print("Capturing Settings...")
    page.click("button[data-page='settings']")
    time.sleep(1)
    page.screenshot(path=str(media_dir / "settings.png"))
    
    context.close()
    browser.close()

print("Screenshots and video recording completed successfully!")
