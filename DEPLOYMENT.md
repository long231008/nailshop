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
- `NOTIFICATION_BACKEND=null` until a real SMS/email provider is wired up.
  Never `console` in production — that writes one-time codes into the logs.
- Cloudinary credentials: with them set, design photos go to Cloudinary; without
  them, photos are written to the container's `uploads/` directory, which is
  lost on restart unless a volume is mounted.

## 4. Database

- Run `alembic upgrade head` as part of the release, before the new code serves
  traffic.
- Use a database user limited to the application schema; the app never needs
  `CREATE DATABASE` or superuser rights.
- Tests write to the database they are pointed at. Give CI its own
  `DATABASE_URL` so a stray run can never touch production data.

## 5. After deploying

Check the startup log for `INSECURE FOR PRODUCTION` lines, then confirm from
outside:

```bash
curl -sI https://api.nailzinc.co.uk/health | grep -i strict-transport-security
curl -s  https://api.nailzinc.co.uk/health   # {"status":"ok"}
```
