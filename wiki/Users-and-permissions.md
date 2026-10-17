# Users and permissions

Tuya Local 0.3.0 supports up to 32 local accounts with two roles. These accounts are independent of the Tuya / Smart Life cloud account.

| Capability | Administrator | Read-only |
| --- | --- | --- |
| Watch all configured cameras in HD or SD | Yes | Yes |
| Use fullscreen, local snapshots and sentinel layouts | Yes | Yes |
| Start, maintain or stop their own web players | Yes | Yes |
| Change camera settings or use PTZ | Yes | No |
| Manage Tuya login, refresh cameras or control the engine directly | Yes | No |
| Configure AI, view server event history or clear events | Yes | No |
| View diagnostics and manage local user accounts | Yes | No |

Permissions are enforced by the HTTP server on every request. Hiding a button is not the authorization boundary. Player tokens are bound to their login session: knowing another user's player token does not permit stopping or claiming that player.

## First administrator and migration

If no web password was configured, open **Server settings** and create the first administrator with a username and a unique password of at least 12 characters. Do this while access is restricted to localhost or a trusted network. Before this first account exists, the original unprotected local setup mode remains available.

An existing single server password, including one initialized through the environment, becomes the **admin** account automatically. Sign in using username `admin` and the existing password. Old sessions expire during the upgrade. The old verifier remains valid; no password reset is required. A later account edit stores the version 2 account format atomically.

## Add and manage accounts

Open **Server settings → Users → Add a user**, enter a unique username, choose **Read-only** or **Administrator**, then set and confirm the password. New users default to read-only.

Use **Edit** to change another account's role or reset its password. Leave the password fields blank to keep its password. **Delete** asks for confirmation and removes that account. Password resets, role changes and deletion revoke the affected account's sessions and close its existing web players. Other users stay signed in.

You cannot delete or change the role of the administrator account you are currently using. Use **My password** to change your own password; it requires your current password and revokes your other sessions. This keeps at least one administrator available.

## Storage and scope

Usernames, roles, random salts and scrypt verifiers are stored in the private `web-access.json` file in the server data volume or desktop data directory. No plaintext account password is stored by the interface. Sessions remain in server memory, expire after 12 hours and do not survive restart. Account data is excluded from Git and Docker build contexts.

These roles govern the web interface and its API, including integrated playback. Direct RTSP connections such as VLC do not use these web accounts: retain the default localhost binding or protect RTSP separately on a trusted network/VPN. Video and AI processing remain local. See [Network and privacy](Network-and-privacy).

![Administrator user management with synthetic accounts](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/users.png)

![Read-only interface with synthetic cameras](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/read-only.png)
