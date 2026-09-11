# Retail Shop Manager — Web Version

A browser-based version of Retail Shop Manager: the same inventory, sales,
reports, and settings as the desktop app, but reachable from any device on
your network — Windows and Mac computers, Android and iOS phones, tablets —
all sharing **one live, synced database** instead of each device having its
own separate copy.

This reuses `database.py` completely unchanged from the desktop version —
same login system, same password hashing, same sales/inventory logic. Only
the interface is different: Flask + HTML pages instead of Tkinter windows.

## Requirements

- **Python 3.8+**
- **Flask** — the web framework
- **openpyxl** — *optional*, only for the "Export to Excel" buttons

```bash
pip install -r requirements.txt
```

## Running it

```bash
python3 app.py
```

You'll see something like:
```
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5000
 * Running on http://192.168.1.23:5000
```

- **On the same computer:** open `http://127.0.0.1:5000` (or `localhost:5000`)
  in a browser.
- **From another device on the same WiFi/network** (a phone, tablet, another
  PC): use the *second* address shown — the one that isn't `127.0.0.1`. That's
  this computer's address on your network. Type that into the other device's
  browser, e.g. `http://192.168.1.23:5000`.
  - Find that address anytime with `ipconfig` (Windows) or `ifconfig` /
    `ip addr` (Mac/Linux) — look for "IPv4 Address" on your WiFi/Ethernet adapter.
  - All devices must be on the **same network** (same WiFi) for this to work.
  - Bookmark the address on each device, or set a static/reserved IP for this
    computer in your router settings so the address doesn't change later.

First visit triggers the same **Setup** screen as the desktop app — create
an admin account. After that, anyone on any device logs in at `/login` with
their own username and password, same as before, and they all see the same
live inventory, sales, and reports.

## How this differs from the desktop version

| | Desktop (Tkinter) | Web (Flask) |
|---|---|---|
| Where it runs | One computer only | Any device with a browser |
| Data | Separate `shop.db` per install | One shared `shop.db` on the server computer |
| Multi-user | Users share the same install, one at a time | Multiple people, multiple devices, at the same time |
| Chart | Drawn with matplotlib | Drawn with Chart.js (loaded from a CDN — needs internet once per page load; figures still show without it) |

## Keeping it running

The `python3 app.py` command needs to keep running on whichever computer is
acting as the "server" — closing that terminal stops the app for everyone.
For a shop that's open all day, a few options:

- Simplest: leave that computer on and the terminal window open during
  business hours.
- Better: run it as a background service so it survives reboots and log-outs.
  On Windows, tools like [NSSM](https://nssm.cc/) can wrap `python app.py`
  into a proper Windows service. On Linux, a `systemd` service unit does the
  same thing.
- The built-in server (`app.run(...)`) is fine for a single shop's traffic,
  but its own startup message says it's a *development* server. For anything
  more demanding, run it behind a production WSGI server like `waitress`
  (Windows-friendly) or `gunicorn` (Linux/Mac) instead of calling
  `python3 app.py` directly.

## Security notes

- Passwords are hashed the same way as the desktop app (salted
  PBKDF2-HMAC-SHA256) — never stored in plain text.
- Every form submission is protected against **CSRF** (a malicious website
  tricking your browser into submitting a form here without you meaning to)
  using a per-session token tied to Flask's signed session cookie.
- This app is designed for **trusted local-network use** — a shop's own
  WiFi, where only staff devices connect. It's currently plain HTTP, not
  HTTPS, so anyone who could intercept traffic on the same network could
  theoretically read login requests. That's a reasonable trade-off on a
  private home/shop WiFi network, but if you ever expose this beyond your
  local network (over the open internet), it needs HTTPS in front of it
  (e.g. via a reverse proxy like Caddy or nginx, which also makes HTTPS
  setup close to automatic) plus a production WSGI server as mentioned above.

## Concurrency note

SQLite handles a small shop's normal traffic fine, but it isn't built for
heavy simultaneous writes from many devices at once. If this ever needs to
support a busy shop with several tills selling at the exact same moment,
or truly multi-location access over the internet, the natural upgrade is
swapping SQLite for a networked database like PostgreSQL — again, only
`database.py` would need to change, not the web pages.
