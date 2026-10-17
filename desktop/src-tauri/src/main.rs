#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde::Serialize;
use std::{
    fs,
    net::{IpAddr, TcpListener},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use url::Url;

#[derive(Default)]
struct Server {
    child: Mutex<Option<Child>>,
    address: Mutex<Option<String>>,
}
#[derive(Serialize)]
struct Status {
    running: bool,
    url: Option<String>,
}

fn validate_address(address: &str) -> Result<Url, String> {
    let url = Url::parse(address.trim()).map_err(|_| "Adresse du serveur invalide.")?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        return Err("Utilise l’adresse racine HTTP(S), sans identifiants ni paramètres.".into());
    }
    if url.scheme() == "http" {
        let local = url.host_str().is_some_and(|host| {
            host == "localhost"
                || host.parse::<IpAddr>().is_ok_and(|ip| match ip {
                    IpAddr::V4(ip) => ip.is_private() || ip.is_loopback() || ip.is_link_local(),
                    IpAddr::V6(ip) => {
                        ip.is_loopback() || ip.is_unique_local() || ip.is_unicast_link_local()
                    }
                })
        });
        if !local {
            return Err(
                "HTTP est réservé au réseau local. Utilise HTTPS pour une adresse publique.".into(),
            );
        }
    }
    Ok(url)
}

async fn probe(url: &Url) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "Connexion indisponible.")?;
    let response = client
        .get(url.join("api/state").map_err(|_| "Adresse invalide.")?)
        .send()
        .await
        .map_err(|_| "Serveur inaccessible. Vérifie son adresse et sa connexion.")?;
    if !response.status().is_success() || response.content_length().unwrap_or(0) > 1_000_000 {
        return Err("Ce serveur ne répond pas comme Tuya Local.".into());
    }
    let value: serde_json::Value = response
        .json()
        .await
        .map_err(|_| "Réponse du serveur invalide.")?;
    if value.get("localOnly") != Some(&serde_json::Value::Bool(true))
        || !value.get("cameras").is_some_and(|c| c.is_array())
    {
        return Err("Ce serveur ne répond pas comme Tuya Local.".into());
    }
    Ok(())
}

fn open_dashboard(app: &tauri::AppHandle, url: Url) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("dashboard") {
        window
            .close()
            .map_err(|_| "Impossible de fermer l’ancien lecteur.")?;
    }
    let allowed = url.origin();
    WebviewWindowBuilder::new(app, "dashboard", WebviewUrl::External(url))
        .title("Tuya Local — Caméras")
        .inner_size(1280., 850.)
        .min_inner_size(390., 600.)
        .on_navigation(move |target| target.origin() == allowed)
        .build()
        .map_err(|_| "Impossible d’ouvrir le lecteur.")?;
    Ok(())
}

#[tauri::command]
async fn connect_server(app: tauri::AppHandle, address: String) -> Result<(), String> {
    let url = validate_address(&address)?;
    probe(&url).await?;
    open_dashboard(&app, url)
}

fn free_port() -> Result<u16, String> {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|_| "Aucun port local disponible.".into())
}

#[tauri::command]
fn server_status(state: tauri::State<Server>) -> Status {
    let mut child = state.child.lock().unwrap();
    let running = child
        .as_mut()
        .is_some_and(|c| c.try_wait().ok().flatten().is_none());
    Status {
        running,
        url: if running {
            state.address.lock().unwrap().clone()
        } else {
            None
        },
    }
}

#[tauri::command]
async fn start_local(app: tauri::AppHandle) -> Result<String, String> {
    let state = app.state::<Server>();
    let current = {
        let mut child = state.child.lock().unwrap();
        if child
            .as_mut()
            .is_some_and(|c| c.try_wait().ok().flatten().is_none())
        {
            state.address.lock().unwrap().clone()
        } else {
            None
        }
    };
    if let Some(address) = current {
        let url = validate_address(&address)?;
        probe(&url).await?;
        open_dashboard(&app, url)?;
        return Ok(address);
    }
    let root = app
        .path()
        .resource_dir()
        .map_err(|_| "Dossier de l’application inaccessible.")?
        .join("runtime");
    let executable = root.join(if cfg!(windows) {
        "tuya-local-server.exe"
    } else {
        "tuya-local-server"
    });
    if !executable.is_file() {
        return Err("Le serveur intégré n’est pas inclus dans cette compilation. Tu peux connecter un serveur existant.".into());
    }
    let data = app
        .path()
        .app_data_dir()
        .map_err(|_| "Dossier de données inaccessible.")?;
    fs::create_dir_all(&data).map_err(|_| "Impossible de créer le dossier de données.")?;
    let http = free_port()?;
    let rtsp = free_port()?;
    let mut command = Command::new(executable);
    command
        .current_dir(&data)
        .env("TUYA_DESKTOP_DATA", &data)
        .env("TUYA_HTTP_HOST", "127.0.0.1")
        .env("TUYA_HTTP_PORT", http.to_string())
        .env("TUYA_RTSP_PORT", rtsp.to_string())
        .env("TUYA_BRIDGE_PUBLIC_HOST", "127.0.0.1")
        .env("TUYA_RTSP_BIND", "127.0.0.1")
        .env("TUYA_BROWSER_TCP_MIN", "52200")
        .env("TUYA_LOCAL_MEDIA_ONLY", "1")
        .env("TUYA_HEVC_TRANSPORT", "rtp")
        .env("TUYA_HEVC_EAGER_START", "1")
        .env("TUYA_MOBILE_SIGNALING", "0")
        .env("TUYA_MOBILE_WEBRTC", "0")
        .env("TUYA_NATIVE_WAKE_HELPER", "")
        .env_remove("TUYA_WEBRTC_HOST_IP")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let child = command
        .spawn()
        .map_err(|_| "Impossible de démarrer le serveur intégré.")?;
    let address = format!("http://127.0.0.1:{http}/");
    *state.child.lock().unwrap() = Some(child);
    *state.address.lock().unwrap() = Some(address.clone());
    let url = validate_address(&address)?;
    let deadline = Instant::now() + Duration::from_secs(40);
    loop {
        if probe(&url).await.is_ok() {
            break;
        }
        if Instant::now() > deadline {
            stop_owned(&state);
            return Err(
                "Le serveur local n’a pas démarré. Réessaie ou utilise un serveur existant.".into(),
            );
        }
        tauri::async_runtime::spawn_blocking(|| std::thread::sleep(Duration::from_millis(250)))
            .await
            .map_err(|_| "Démarrage interrompu.")?;
    }
    open_dashboard(&app, url)?;
    Ok(address)
}

fn stop_owned(state: &Server) {
    if let Some(mut child) = state.child.lock().unwrap().take() {
        drop(child.stdin.take());
        let deadline = Instant::now() + Duration::from_secs(12);
        while child.try_wait().ok().flatten().is_none() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
        }
        if child.try_wait().ok().flatten().is_none() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    *state.address.lock().unwrap() = None;
}
#[tauri::command]
async fn stop_local(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("dashboard") {
        let _ = window.close();
    }
    tauri::async_runtime::spawn_blocking(move || stop_owned(&app.state::<Server>()))
        .await
        .map_err(|_| "Arrêt interrompu.")?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage(Server::default())
        .invoke_handler(tauri::generate_handler![
            connect_server,
            start_local,
            stop_local,
            server_status
        ])
        .setup(|app| {
            use tauri::{
                menu::{Menu, MenuItem},
                tray::TrayIconBuilder,
            };
            let show = MenuItem::with_id(app, "show", "Tuya Local", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quitter / Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("launcher") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "dashboard" && matches!(event, tauri::WindowEvent::Destroyed) {
                if let Some(launcher) = window.app_handle().get_webview_window("launcher") {
                    let _ = launcher.show();
                    let _ = launcher.set_focus();
                }
            }
            if window.label() == "launcher" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Tuya Local could not start")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                stop_owned(&app.state::<Server>());
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn accepts_local_and_secure_remote_servers() {
        for address in [
            "http://127.0.0.1:18788/",
            "http://192.168.1.10:18788/",
            "https://camera.example/",
        ] {
            assert!(validate_address(address).is_ok());
        }
    }
    #[test]
    fn rejects_unsafe_or_ambiguous_addresses() {
        for address in [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://public.example/",
            "https://user:password@camera.example/",
            "https://camera.example/?key=secret",
            "https://camera.example/path",
        ] {
            assert!(validate_address(address).is_err());
        }
    }
}
