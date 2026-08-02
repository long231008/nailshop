# Production deployment checklist

The app refuses nothing at boot, but it logs `INSECURE FOR PRODUCTION: ...`
warnings for the settings below. Check the startup log after every deploy — an
empty warning list is the goal.

## 1. HTTPS is mandatory

Everything the API carries — one-time codes, session tokens, customer phone
numbers and addresses — travels in the request. Over plain HTTP anyone on the
same network reads and replays all of it, and the `Strict-Transport-Security`
header the app already sends is ignored by browsers unless the page is served
over TLS.

Terminate TLS at the ingress/proxy (Caddy, nginx, or a managed load balancer)
and redirect all HTTP traffic to HTTPS. With Kubernetes, the manifests in `k8s/`
expect cert-manager to issue the certificate; add the TLS block and the
redirect annotation to `k8s/ingress.yaml`.

Behind a proxy, also set:

```
TRUST_PROXY_HEADERS=true
```

so rate limiting counts real client IPs instead of the proxy's single address.
Only enable this when a proxy you control is the only way in — otherwise
callers can spoof `X-Forwarded-For` and dodge the limits.

## 2. Name your domains — never `*`

```
CORS_ALLOWED_ORIGINS=https://nailzinc.co.uk
ALLOWED_HOSTS=api.nailzinc.co.uk
FRONTEND_URL=https://nailzinc.co.uk
BACKEND_PUBLIC_URL=https://api.nailzinc.co.uk
GOOGLE_OAUTH_REDIRECT_URI=https://api.nailzinc.co.uk/app/auth/google/callback
FACEBOOK_OAUTH_REDIRECT_URI=https://api.nailzinc.co.uk/app/auth/facebook/callback
```

`CORS_ALLOWED_ORIGINS` lists the websites a browser may call this API from.
With `*` any site on the internet can script requests against the API using a
visitor's browser. List only your own frontend.

`ALLOWED_HOSTS` lists the `Host` headers the API answers to. It blocks
host-header injection, where a request claiming `Host: evil.example` makes the
app generate links pointing at the attacker's domain. List only your API's
domain(s).

Anything that health-checks the API must send one of these hosts. Kubernetes
probes dial the pod IP, so `k8s/deployment.yaml` sets an explicit `Host`
header on all three probes — without it every probe gets `400 Invalid host
header` and the pod never becomes ready. Do the same for any external
uptime monitor.

The frontend needs its own `.env` at build time:

```
VITE_API_BASE_URL=https://api.nailzinc.co.uk
VITE_FACEBOOK_PAGE_URL=https://m.me/YourPage
```

## 3. Secrets and delivery

- `JWT_SECRET_KEY`: 32+ random characters, unique per environment. Rotating it
  signs everyone out, which is the intended emergency action.
- `PAYMENT_WEBHOOK_SECRET`: must match the payment provider's configuration, or
  every webhook is rejected as unsigned.
- `NOTIFICATION_BACKEND=live` once the providers below are configured. Never
  `console` in production — that writes one-time codes into the logs — and
  `null` means customers never receive their codes at all.
- Cloudinary credentials: with them set, design photos go to Cloudinary; without
  them, photos are written to the container's `uploads/` directory, which is
  lost on restart unless a volume is mounted.

## 4. Message delivery

Customers register with either a phone number or an email address, and every
message — one-time codes, deposit links, booking confirmations, design quotes —
goes back on that same channel. Set `NOTIFICATION_BACKEND=live` and configure
whichever providers you need; a channel with no provider is logged as
undelivered rather than failing the request.

### Email — free, and enough on its own

Any provider that hands out SMTP credentials works. Free tiers comfortably
cover a salon's volume (a few hundred messages a month):

| Provider | Free allowance | SMTP host | Notes |
| --- | --- | --- | --- |
| Brevo | ~300/day | `smtp-relay.brevo.com:587` | Most generous; verify the domain |
| Resend | ~3,000/month | `smtp.resend.com:587` | Simplest setup |
| Mailgun | ~100/day | `smtp.mailgun.org:587` | Asks for a card |
| Gmail | ~500/day | `smtp.gmail.com:587` | Needs an **app password**, not the account password. Zero setup, but mail shows the Gmail address and deliverability is weaker — fine to start, move to a real provider before opening properly |

```
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_USE_TLS=true
EMAIL_FROM_ADDRESS=hello@nailzinc.co.uk
EMAIL_FROM_NAME=Nailzinc
```

Verify your sending domain with the provider (SPF/DKIM records). Without it,
codes land in spam — which looks exactly like the app being broken.

Using SendGrid's API instead? Set `SENDGRID_API_KEY` and leave `SMTP_HOST`
empty; the API takes priority.

### SMS — Twilio, and it is never free

Carriers charge per message, so no provider gives away SMS. UK messages run
about £0.04 each; roughly £4 per 100 logins. **SMS is optional**: leave the
Twilio settings blank and the app runs on email alone, which is why the login
page offers both phone and email.

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=...
TWILIO_SENDER=Nailzinc          # or +447700900000
SMS_COUNTRY_CODE=+44
```

An alphanumeric sender ID shows the salon's name instead of a strange number,
which matters for codes people are asked to trust. UK registration takes a few
days; a purchased number works immediately. Numbers are stored as customers
type them (`07488566218`) and converted to E.164 (`+447488566218`) on the way
out, using `SMS_COUNTRY_CODE`.

With `PREFER_EMAIL_OVER_SMS=true` (the default) a customer who has both is
reached by email, so SMS is only spent on people who gave nothing else.

Send yourself a code from the login page after deploying — it is the fastest
end-to-end check that the whole chain works.

## 5. Database

- Run `alembic upgrade head` as part of the release, before the new code serves
  traffic.
- Use a database user limited to the application schema; the app never needs
  `CREATE DATABASE` or superuser rights.
- Tests write to the database they are pointed at. Give CI its own
  `DATABASE_URL` so a stray run can never touch production data.

## 6. After deploying

Check the startup log for `INSECURE FOR PRODUCTION` lines, then confirm from
outside:

```bash
curl -sI https://api.nailzinc.co.uk/health | grep -i strict-transport-security
curl -s  https://api.nailzinc.co.uk/health   # {"status":"ok"}
```
