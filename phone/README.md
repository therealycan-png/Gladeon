# Gladeon phone scripts (Termux)

Install Termux and Termux:API from F-Droid. Then:

```
pkg install curl jq termux-api
mkdir -p ~/.gladeon && chmod 700 ~/.gladeon
```

Copy these files into `~/.gladeon/` from the client bundle (`gladeon-admin client-bundle`, spec 3.3.7):

| File | Contents |
|---|---|
| `config` | `PC_IP_ETH=…`, `PC_IP_WLAN=…`, `SPKI_PIN=…` (base64 SHA-256 of the server key), optional `PORT=8443` |
| `cert.pem` | the proxy's self-signed certificate |
| `auth.header` | one line: `Authorization: Bearer <token>` (mode `0600`) |

The token is read by curl from `auth.header` and the passphrase goes through stdin, so neither shows up in the process list.

| Script | Does |
|---|---|
| `gladeon-ask [--deep\|--quick] [text]` | asks; with no text it listens once. It prints the answer, speaks it, and polls `/result` if the answer takes long |
| `gladeon-unlock` | asks for the vault passphrase without echo |
| `gladeon-lock` | locks the vault |
| `gladeon-status` | short status |
| `gladeon-shutdown [--reboot]` | asks you to type SHUTDOWN or REBOOT first |

Put shortcuts in `~/.shortcuts/` for the Termux:Widget home-screen buttons.
