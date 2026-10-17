# Users and permissions

Tuya Local supports up to 32 local accounts with two roles. These accounts are independent of the Tuya / Smart Life cloud account.

| Capability | Administrator | Read-only |
| --- | --- | --- |
| Watch authorized cameras in HD or SD | All cameras | Assigned cameras |
| Use fullscreen, local snapshots and sentinel layouts | Yes | Yes |
| Start, maintain or stop their own web players | Yes | Yes |
| Change camera settings or use PTZ | Yes | No |
| Manage Tuya login, refresh cameras or control the engine directly | Yes | No |
| Configure AI, view server event history or clear events | Yes | No |
| View diagnostics and manage local user accounts | Yes | No |

Permissions are enforced by the HTTP server on every request. Hiding a button is not the authorization boundary. Player tokens are bound to their login session: knowing another user's player token does not permit stopping or claiming that player.

## First administrator and migration

If no web password was configured, open **Server settings** and create the first administrator with a username and a unique password of at least 12 characters. Do this while access is restricted to localhost or a trusted network. Before this first account exists, the original unprotected local setup mode remains available.

An existing single server password, including one initialized through the environment, becomes the **admin** account automatically. Sign in using username `admin` and the existing password. Old sessions expire during the upgrade. The old verifier remains valid; no password reset is required. A later account edit stores the current account format atomically.

## Add and manage accounts

Open **Server settings → Users → Add a user**, enter a unique username, choose **Read-only** or **Administrator**, then set and confirm the password. New users default to read-only.

Use **Edit** to change another account's role or reset its password. Leave the password fields blank to keep its password. **Delete** asks for confirmation and removes that account. Password resets, role changes and deletion revoke the affected account's sessions and close its existing web players. Other users stay signed in.

You cannot delete or change the role of the administrator account you are currently using. Use **My password** to change your own password; it requires your current password and revokes your other sessions. This keeps at least one administrator available.

## Camera permissions (0.4.0)

In **Server settings → Users → Edit**, choose **Allowed cameras** for each read-only account. You can select individual cameras, no cameras, or explicitly allow **All cameras, including future ones**. New read-only accounts default to no cameras. Selecting every current camera individually does not grant future cameras. Administrators always retain full access because they manage the server and its permissions.

Names and identifiers for excluded cameras are filtered out of the read-only camera list. Direct HD/SD and WebRTC/MP4 playback requests for an excluded or unknown camera receive the same refusal before any wake-up, media negotiation or decoder starts. Player requests also recheck the account's current camera grants. Permission changes revoke the affected account's sessions and stop its web players; the user signs in again with the updated selection.

Assignments use stable camera identifiers, so renaming a camera does not grant or remove access. Assignments to temporarily unavailable cameras are preserved when editing an account or resetting its password. Invalid selections fail without changing the existing record. Saved camera grants are validated on startup; malformed version 3 data stops startup instead of expanding access.

Existing version 1/2 accounts migrate with their previous all-camera access. This preserves existing rights during the upgrade; administrators can then narrow read-only accounts. New writes use version 3 account records, with `cameraIds: null` for all-camera access and an explicit list (including an empty list) for restricted access. These grants survive restart.

![Camera selection with synthetic accounts and cameras](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/camera-permissions.png)

![Reader sees only the assigned camera](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/cameras-filtered.png)

## Storage and scope

Usernames, roles, random salts and scrypt verifiers are stored in the private `web-access.json` file in the server data volume or desktop data directory. No plaintext account password is stored by the interface. Sessions remain in server memory, expire after 12 hours and do not survive restart. Account data is excluded from Git and Docker build contexts.

## VLC and direct RTSP (0.5.0)

VLC uses the same Tuya Local username/password and camera grants as integrated playback. Open the camera's normal HD/SD RTSP URL and enter your Tuya Local credentials when prompted. Prefer RTSP over TCP. Do not add passwords to shared links or playlists.

Anonymous RTSP is disabled, including on localhost. Configure the first administrator in Tuya Local settings before connecting VLC. A viewer can only access assigned cameras and cannot use the audio backchannel. Changing the path or adding AI query parameters does not bypass authorization. Account deletion or a password, role or camera-grant change closes existing RTSP connections, normally within one second (up to three seconds if the authorization service stops responding). A stopped authorization service denies new connections.

The integrated viewer, local AI and settings wake-up use a separate ephemeral internal credential, never a localhost exemption. Internal media connections stay on loopback. Video and AI processing remain local. RTSP uses Basic authentication for compatibility with existing password verifiers and VLC; this does not encrypt credentials or video. Keep the localhost default or use a trusted local network/VPN. See [Network and privacy](Network-and-privacy).

![Administrator user management with synthetic accounts](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/users.png)

![Read-only interface with synthetic cameras](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/read-only.png)
