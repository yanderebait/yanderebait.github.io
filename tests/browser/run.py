# Run with: python3 tests/browser/run.py   (needs playwright, Pillow, Google Chrome, ffmpeg)
# Drives shop.html and admin.html in headless Chrome against a stand-in back end
# that applies the same who-can-do-what rules as supabase/setup.sql.
import base64, io, json, os, subprocess, sys, threading, time, uuid, functools, http.server
from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(os.path.dirname(HERE))
SHOTS = os.environ.get("SHOTS", "/tmp/shop-shots")
os.makedirs(SHOTS, exist_ok=True)
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
MOCK_URL = "http://mock-backend.test"
PHOTOS = os.environ.get("PHOTOS", "")  # folder of real photos; plain colours are used without it

# ---------------------------------------------------------------- back end
db, files, flags = [], {}, {"fail_select": False}
TOKENS = {"owner-token": "owner", "stranger-token": "stranger"}

def jpeg(path, side):
    im = Image.open(path).convert("RGB") if os.path.exists(path) else Image.new("RGB", (900, 1200), (85, 96, 122))
    im.thumbnail((side, side))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=80); return buf.getvalue()

def seed():
    db.clear(); files.clear()
    def piece(title, price, status, photos, desc="", days=0):
        pid = str(uuid.uuid4()); media = []
        for i, src in enumerate(photos):
            p, t = f"{pid}/{i}.jpg", f"{pid}/{i}-small.jpg"
            files[p] = ("image/jpeg", jpeg(src, 1400)); files[t] = ("image/jpeg", jpeg(src, 700))
            media.append({"kind": "image", "path": p, "thumb": t, "size": len(files[p][1]) + len(files[t][1])})
        db.append({"id": pid, "title": title, "description": desc, "price_pence": price, "status": status,
                   "media": media, "created_at": f"2026-11-{10+days:02d}T10:00:00Z", "updated_at": "2026-11-16T10:00:00Z"})
        return pid
    ids = {}
    ids["sold"] = piece("Tiny cleaver", 3500, "sold", [f"{PHOTOS}/06 Tiny cleaver/1.jpg", f"{PHOTOS}/06 Tiny cleaver/2.jpg"], "A very small cleaver.", 1)
    ids["sale"] = piece("Feather dagger", 4550, "for_sale", [f"{PHOTOS}/11 Feather dagger/1.jpg", f"{PHOTOS}/11 Feather dagger/2.jpg", f"{PHOTOS}/11 Feather dagger/3.jpg"],
                        "Mild steel display piece.\n\nAbout 20 cm long. Comes with the stand.", 2)
    ids["nfs"] = piece("Dragon scale blade", None, "not_for_sale", [f"{PHOTOS}/12 Dragon scale blade - PIN THIS/1.jpg"], "Made from a horseshoe file.", 3)
    ids["res"] = piece("Bowie-style knife", 6000, "reserved", [f"{PHOTOS}/08 Knife and cat/1.jpg"], "", 4)
    ids["soon"] = piece("Wooden hidden blade", 125000, "coming_soon", [], "", 5)
    ids["draft"] = piece("Secret draft dagger", 2000, "draft", [f"{PHOTOS}/07 Dagger/1.jpg"], "", 6)
    return ids

def backend(raw):
    req = json.loads(raw); op, a = req["op"], req["args"]; who = TOKENS.get(req.get("token"))
    admin = who == "owner"
    denied = {"data": None, "error": {"message": "new row violates row-level security policy"}}
    if op == "signin":
        if a["email"] == "owner@example.test" and a["password"] == "right-password": return json.dumps({"token": "owner-token"})
        if a["email"] == "stranger@example.test" and a["password"] == "right-password": return json.dumps({"token": "stranger-token"})
        return json.dumps({"error": {"message": "Invalid login credentials"}})
    if op == "rpc": return json.dumps({"data": admin, "error": None})
    if op == "upload":
        if not admin or a["bucket"] != "media": return json.dumps(denied)
        if a["path"] in files: return json.dumps({"data": None, "error": {"message": "The resource already exists"}})
        files[a["path"]] = (a["type"], base64.b64decode(a["b64"])); return json.dumps({"data": {"path": a["path"]}, "error": None})
    if op == "remove":
        if not admin: return json.dumps({"data": [], "error": None})
        for p in a["paths"]: files.pop(p, None)
        return json.dumps({"data": [], "error": None})
    if op == "query":
        def match(r):
            for kind, c, v in a["filters"]:
                if kind == "eq" and r.get(c) != v: return False
                if kind == "neq" and r.get(c) == v: return False
            return True
        act = a["action"]
        if act == "select":
            if flags["fail_select"]: return json.dumps({"data": None, "error": {"message": "connection refused"}})
            rows = [r for r in db if match(r) and (admin or r["status"] != "draft")]
            if a.get("order"): rows.sort(key=lambda r: r[a["order"][0]], reverse=not a["order"][1])
            return json.dumps({"data": rows, "error": None})
        if act == "insert":
            if not admin: return json.dumps(denied)
            row = {"description": "", "price_pence": None, "status": "draft", "media": [], **a["row"],
                   "created_at": "2026-11-16T12:00:00Z", "updated_at": "2026-11-16T12:00:00Z"}
            db.append(row); return json.dumps({"data": row, "error": None})
        if act == "update":
            hit = [r for r in db if match(r)] if admin else []
            for r in hit: r.update(a["row"])
            if a.get("single"):
                return json.dumps({"data": hit[0], "error": None} if hit else {"data": None, "error": {"message": "no rows"}})
            return json.dumps({"data": None, "error": None})
        if act == "delete":
            if admin: db[:] = [r for r in db if not match(r)]
            return json.dumps({"data": None, "error": None})
    return json.dumps({"data": None, "error": {"message": "unknown op"}})

# ---------------------------------------------------------------- harness
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(Quiet, directory=SITE))
threading.Thread(target=server.serve_forever, daemon=True).start()

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  -> {detail}"))

def serve_media(route):
    path = route.request.url.split("/storage/v1/object/public/media/", 1)[1]
    hit = files.get(path)
    route.fulfill(status=200, content_type=hit[0], body=hit[1]) if hit else route.fulfill(status=404, body="")

def new_page(browser, connected=True, width=390, height=844, scale=1):
    ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale)
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    ctx.expose_function("__backend", backend)
    ctx.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    if connected:
        ctx.route("**/vendor/supabase.js", lambda r: r.fulfill(path=os.path.join(HERE, "mock-supabase.js"), content_type="text/javascript"))
        ctx.route("**/shop-config.js", lambda r: r.fulfill(body=f'window.SHOP_CONFIG={{supabaseUrl:"{MOCK_URL}",supabaseKey:"test-key"}};', content_type="text/javascript"))
        ctx.route("**/storage/v1/object/public/media/**", serve_media)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    return ctx, page

def make_test_files():
    big = os.path.join(HERE, "big-photo.png")
    Image.new("RGB", (3000, 4000), (90, 15, 30)).save(big)
    clip = os.path.join(HERE, "clip.mp4")
    made = subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x400:rate=10", "-pix_fmt", "yuv420p", clip],
                          capture_output=True).returncode == 0 if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0 else False
    huge = os.path.join(HERE, "huge.mp4")
    with open(huge, "wb") as f: f.truncate(51 * 1024 * 1024)
    return big, (clip if made else None), huge

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    big, clip, huge = make_test_files()

    # ---------- SHOP ----------
    print("\n-- shop page")
    ctx, page = new_page(browser, connected=False)
    page.goto(f"{BASE}/shop.html"); page.wait_for_timeout(600)
    check("shop, not connected: says it isn't open yet", "isn't open yet" in page.inner_text("#notice"), page.inner_text("#notice"))
    check("shop, not connected: no script errors", not page.errors, page.errors)
    ctx.close()

    ids = seed()
    ctx, page = new_page(browser, scale=2)
    page.goto(f"{BASE}/shop.html"); page.wait_for_selector(".piece")
    titles = page.eval_on_selector_all(".piece .title", "els => els.map(e => e.textContent)")
    check("shop shows the 5 published pieces", len(titles) == 5, titles)
    check("shop hides the draft", "Secret draft dagger" not in titles, titles)
    check("shop order is for sale, reserved, coming soon, not for sale, sold",
          titles == ["Feather dagger", "Bowie-style knife", "Wooden hidden blade", "Dragon scale blade", "Tiny cleaver"], titles)
    cards = page.eval_on_selector_all(".piece", "els => els.map(e => [e.querySelector('.price').textContent, e.querySelector('.stamp').textContent])")
    check("shop cards show price and status in words",
          cards == [["£45.50", "For sale"], ["£60", "Reserved"], ["£1,250", "Coming soon"], ["Price on request", "Not for sale"], ["£35", "Sold"]], cards)
    loaded = page.eval_on_selector_all(".piece img", "els => els.map(e => e.complete && e.naturalWidth > 0)")
    page.wait_for_timeout(500)
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("shop has no sideways scroll on a phone", over == 0, over)
    page.screenshot(path=f"{SHOTS}/shop-phone.png", full_page=True)

    page.click(".piece a")
    page.wait_for_selector("dialog[open]")
    check("opening a piece shows its title, price and status",
          [page.inner_text("#piece-title"), page.inner_text("#piece-price"), page.inner_text("#piece-stamp")] == ["Feather dagger", "£45.50", "For sale"])
    check("opened piece keeps the description's line breaks", "About 20 cm long" in page.inner_text("#piece-words") and "\n" in page.inner_text("#piece-words"))
    check("a for-sale piece shows the How to buy button", page.is_visible("#piece-buy") and page.get_attribute("#piece-buy", "href") == "how-to-buy.html")
    check("the address bar holds a link to that piece", page.url.endswith("#piece-" + ids["sale"]), page.url)
    check("gallery has 3 photos", page.locator("#track .slide").count() == 3)
    page.click("#next"); page.wait_for_timeout(900)
    check("Next moves to photo 2", page.inner_text("#where") == "Showing 2 of 3", page.inner_text("#where"))
    page.click("#prev"); page.click("#prev"); page.wait_for_timeout(900)
    check("Previous from photo 1 wraps to photo 3", page.inner_text("#where") == "Showing 3 of 3", page.inner_text("#where"))
    page.click("#dots button:nth-child(1)"); page.wait_for_timeout(900)
    page.screenshot(path=f"{SHOTS}/shop-piece-phone.png")
    page.click("#piece-link"); page.wait_for_timeout(200)
    check("Copy link puts the piece link on the clipboard", page.evaluate("navigator.clipboard.readText()").endswith("#piece-" + ids["sale"]))
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    check("Escape closes the piece and clears the link", page.locator("dialog[open]").count() == 0 and "#piece-" not in page.url, page.url)

    page.goto(f"{BASE}/shop.html#piece-{ids['sold']}"); page.wait_for_selector("dialog[open]")
    check("a shared link opens straight on that piece", page.inner_text("#piece-title") == "Tiny cleaver")
    check("a sold piece says Sold and hides How to buy", page.inner_text("#piece-state") == "Sold." and not page.is_visible("#piece-buy"))
    page.goto(f"{BASE}/shop.html#piece-{ids['draft']}"); page.reload(); page.wait_for_selector(".piece")
    check("a link to a draft opens nothing", page.locator("dialog[open]").count() == 0)
    check("shop page: no script errors", not page.errors, page.errors)
    ctx.close()

    ctx, page = new_page(browser, width=1280, height=900)
    page.goto(f"{BASE}/shop.html"); page.wait_for_selector(".piece"); page.wait_for_timeout(500)
    page.screenshot(path=f"{SHOTS}/shop-desktop.png", full_page=True)
    page.click(".piece a"); page.wait_for_selector("dialog[open]"); page.wait_for_timeout(500)
    page.screenshot(path=f"{SHOTS}/shop-piece-desktop.png")
    ctx.close()

    flags["fail_select"] = True
    ctx, page = new_page(browser); page.goto(f"{BASE}/shop.html"); page.wait_for_timeout(600)
    check("shop says so when the back end can't be reached", "didn't load" in page.inner_text("#notice"), page.inner_text("#notice"))
    ctx.close(); flags["fail_select"] = False
    db.clear()
    ctx, page = new_page(browser); page.goto(f"{BASE}/shop.html"); page.wait_for_timeout(600)
    check("shop with no pieces says nothing is on the shelf", "Nothing on the shelf" in page.inner_text("#notice"), page.inner_text("#notice"))
    ctx.close()

    # ---------- ADMIN ----------
    print("\n-- admin page")
    ctx, page = new_page(browser, connected=False)
    page.goto(f"{BASE}/admin.html"); page.wait_for_timeout(500)
    check("admin, not connected: says not connected", page.is_visible("#view-setup"))
    ctx.close()

    ids = seed()
    ctx, page = new_page(browser, scale=2)
    page.goto(f"{BASE}/admin.html"); page.wait_for_selector("#view-login:not([hidden])")
    check("admin starts at the log in form", page.is_visible("#login-form"))
    page.fill("#login-email", "owner@example.test"); page.fill("#login-password", "wrong"); page.click("#login-submit")
    page.wait_for_function("document.getElementById('login-status').textContent.includes('right')")
    check("wrong password is refused with a plain message", page.inner_text("#login-status") == "That email or password isn't right.", page.inner_text("#login-status"))
    page.click("#show-password")
    check("Show reveals the password", page.get_attribute("#login-password", "type") == "text" and page.get_attribute("#show-password", "aria-pressed") == "true")
    page.screenshot(path=f"{SHOTS}/admin-login-phone.png")

    page.fill("#login-email", "stranger@example.test"); page.fill("#login-password", "right-password"); page.click("#login-submit")
    page.wait_for_selector("#view-denied:not([hidden])")
    check("a login that isn't on the admin list is turned away", page.is_visible("#view-denied") and not page.is_visible("#view-list"))
    page.click("#sign-out"); page.wait_for_selector("#view-login:not([hidden])")

    page.fill("#login-email", "owner@example.test"); page.fill("#login-password", "right-password"); page.click("#login-submit")
    page.wait_for_selector("#view-list:not([hidden])")
    check("owner sees all 6 pieces including the draft", page.locator("#list .row").count() == 6 and "Secret draft dagger" in page.inner_text("#list"))
    check("storage meter shows a figure", page.inner_text("#storage-text").startswith("Storage used:") and "of 1,000 MB" in page.inner_text("#storage-text"), page.inner_text("#storage-text"))
    page.screenshot(path=f"{SHOTS}/admin-list-phone.png", full_page=True)

    row = page.locator("#list .row", has_text="Bowie-style knife")
    row.locator("select").select_option("sold"); page.wait_for_function("document.getElementById('list-status').textContent.includes('Sold')")
    check("changing a status in the list saves it", next(r for r in db if r["id"] == ids["res"])["status"] == "sold", page.inner_text("#list-status"))

    # add a piece
    page.click("#add-piece"); page.wait_for_selector("#view-edit:not([hidden])")
    page.click("#save")
    check("saving with no title is refused", page.inner_text("#f-title-error") == "Give the piece a title." and len(db) == 6)
    page.fill("#f-title", "Test dagger"); page.fill("#f-price", "abc"); page.click("#save")
    check("a price with letters is refused", "number" in page.inner_text("#f-price-error") and len(db) == 6, page.inner_text("#f-price-error"))
    page.fill("#f-price", "45.50")
    check("the price complaint clears once the price is fixed", page.inner_text("#f-price-error") == "" and page.get_attribute("#f-price", "aria-invalid") is None)
    page.fill("#f-description", "Line one.\nLine two."); page.select_option("#f-status", "for_sale")
    page.set_input_files("#f-files", huge)
    check("a 51 MB video is refused with the limit stated", "limit is 50 MB" in page.inner_text("#f-files-error") and page.locator("#media-list li").count() == 0, page.inner_text("#f-files-error"))
    page.set_input_files("#f-files", [big] + ([clip] if clip else []))
    expect_media = 2 if clip else 1
    check(f"picked files are listed ({expect_media})", page.locator("#media-list li").count() == expect_media)
    page.screenshot(path=f"{SHOTS}/admin-edit-phone.png", full_page=True)
    before_files = len(files)
    page.click("#save"); page.wait_for_selector("#view-list:not([hidden])", timeout=30000)
    new = next((r for r in db if r["title"] == "Test dagger"), None)
    check("the new piece is saved with the right price and status", new and new["price_pence"] == 4550 and new["status"] == "for_sale" and new["description"] == "Line one.\nLine two.", new)
    check(f"the new piece has {expect_media} media entries", new and len(new["media"]) == expect_media, new and new["media"])
    if new:
        photo = new["media"][0]
        full = Image.open(io.BytesIO(files[photo["path"]][1])); small = Image.open(io.BytesIO(files[photo["thumb"]][1]))
        check("a 3000x4000 photo is stored at 1500x2000", full.size == (1500, 2000), full.size)
        check("its small copy is 525x700", small.size == (525, 700), small.size)
        check("the stored photo is a JPEG under 1 MB", full.format == "JPEG" and len(files[photo["path"]][1]) < 1024 * 1024, len(files[photo["path"]][1]))
        if clip:
            video = new["media"][1]
            check("the video is stored as uploaded", video["kind"] == "video" and files[video["path"]][1] == open(clip, "rb").read())
            check("a still frame was made for the video", "poster" in video and video["poster"] in files, video)
    check("the list confirms the save", "Saved \"Test dagger\" as For sale." == page.inner_text("#list-status"), page.inner_text("#list-status"))

    # edit: remove the photo, cancel paths
    page.locator("#list .row", has_text="Feather dagger").locator("button", has_text="Edit").click()
    page.wait_for_selector("#view-edit:not([hidden])")
    check("the editor opens filled in", page.input_value("#f-title") == "Feather dagger" and page.input_value("#f-price") == "45.50" and page.locator("#media-list li").count() == 3)
    page.fill("#f-title", "Changed title"); page.click("#cancel")
    check("Cancel with unsaved changes asks first", page.is_visible("#discard-confirm"))
    page.click("#discard-no")
    check("Keep editing keeps the changes", page.is_visible("#view-edit") and page.input_value("#f-title") == "Changed title")
    page.click("#cancel"); page.click("#discard-yes"); page.wait_for_selector("#view-list:not([hidden])")
    check("throwing changes away leaves the piece untouched", next(r for r in db if r["id"] == ids["sale"])["title"] == "Feather dagger")

    page.locator("#list .row", has_text="Feather dagger").locator("button", has_text="Edit").click()
    page.wait_for_selector("#view-edit:not([hidden])")
    sale = next(r for r in db if r["id"] == ids["sale"]); first_path, second_path = sale["media"][0]["path"], sale["media"][1]["path"]
    page.locator("#media-list li").nth(0).locator("button", has_text="Remove").click()
    page.locator("#media-list li").nth(0).locator("button", has_text="Move later").click()
    page.fill("#f-price", ""); page.click("#save"); page.wait_for_selector("#view-list:not([hidden])", timeout=30000)
    sale = next(r for r in db if r["id"] == ids["sale"])
    check("removing a photo and reordering is saved", len(sale["media"]) == 2 and sale["media"][1]["path"] == second_path, [m["path"] for m in sale["media"]])
    check("the removed photo's files are deleted from storage", first_path not in files)
    check("an empty price is saved as price on request", sale["price_pence"] is None, sale["price_pence"])

    # delete
    page.locator("#list .row", has_text="Tiny cleaver").locator("button", has_text="Edit").click()
    page.wait_for_selector("#view-edit:not([hidden])")
    cleaver_files = [m["path"] for m in next(r for r in db if r["id"] == ids["sold"])["media"]]
    page.click("#delete")
    check("Delete asks before doing anything", page.is_visible("#delete-confirm") and any(r["id"] == ids["sold"] for r in db))
    page.click("#delete-no")
    check("Keep it keeps the piece", any(r["id"] == ids["sold"] for r in db) and page.is_visible("#view-edit"))
    page.click("#delete"); page.click("#delete-yes"); page.wait_for_selector("#view-list:not([hidden])")
    check("Delete for good removes the piece", not any(r["id"] == ids["sold"] for r in db))
    check("and its photos", not any(f in files for f in cleaver_files))
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("admin has no sideways scroll on a phone", over == 0, over)

    page.reload(); page.wait_for_selector("#view-list:not([hidden])")
    check("reloading keeps you logged in", page.locator("#list .row").count() == len(db))
    page.click("#sign-out"); page.wait_for_selector("#view-login:not([hidden])")
    check("Sign out returns to the log in form", page.is_visible("#login-form") and not page.is_visible("#view-list"))
    check("admin page: no script errors", not page.errors, page.errors)

    # the shop reflects what the admin did
    page.goto(f"{BASE}/index.html"); page.click("a.knife[href='shop.html']"); page.wait_for_selector(".piece")
    check("the Shop blade on the links page opens the shop", page.url.endswith("/shop.html"), page.url)
    page.click("header a.button"); page.wait_for_selector("h1")
    check("How to buy opens the buying guide, which links back to the shop", page.url.endswith("/how-to-buy.html") and page.locator(".billet a[href='shop.html']").count() == 1, page.url)
    page.goto(f"{BASE}/shop.html"); page.wait_for_selector(".piece")
    titles = page.eval_on_selector_all(".piece .title", "els => els.map(e => e.textContent)")
    check("the shop now lists the new piece and not the deleted one", "Test dagger" in titles and "Tiny cleaver" not in titles, titles)
    ctx.close()

    # a logged-out visitor poking the admin page's back end directly
    r = json.loads(backend(json.dumps({"op": "query", "token": None, "args": {"table": "pieces", "action": "insert", "filters": [], "row": {"id": "x", "title": "hack"}}})))
    browser.close()

server.shutdown()
bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)} passed, {len(bad)} failed")
sys.exit(1 if bad else 0)
