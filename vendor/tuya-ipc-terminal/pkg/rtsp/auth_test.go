package rtsp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"tuya-ipc-terminal/pkg/storage"
	"tuya-ipc-terminal/pkg/tuya"
)

func fakeAuthorizer(t *testing.T, handler http.HandlerFunc) *rtspAuthorizer {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	t.Setenv("TUYA_RTSP_AUTH_URL", server.URL+"/authorize")
	t.Setenv("TUYA_RTSP_AUTH_SECRET", strings.Repeat("s", 32))
	return newRTSPAuthorizer()
}
func TestAuthorizationFailsClosedAndNeverUsesProxy(t *testing.T) {
	t.Setenv("TUYA_RTSP_AUTH_URL", "")
	t.Setenv("TUYA_RTSP_AUTH_SECRET", "")
	if newRTSPAuthorizer().check("Basic anything", "", "device", "").Allowed {
		t.Fatal("missing broker allowed playback")
	}
	server := NewRTSPServer(0, nil)
	if server.Start() == nil {
		t.Fatal("standalone engine started without account protection")
	}
	a := fakeAuthorizer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+strings.Repeat("s", 32) {
			t.Error("private broker secret missing")
		}
		http.Error(w, "offline", 503)
	})
	if a.check("Basic ignored", "", "camera", "").Allowed {
		t.Fatal("broker outage allowed playback")
	}
	a.endpoint = "http://192.0.2.1:80/authorize"
	if a.configured() {
		t.Fatal("public broker accepted")
	}
}
func TestAnonymousRTSPGetsChallengeBeforeAnyCameraOrWakeWork(t *testing.T) {
	a := fakeAuthorizer(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `{}`) })
	server := NewRTSPServer(0, nil)
	server.authorizer = a
	left, right := net.Pipe()
	defer right.Close()
	done := make(chan struct{})
	go func() { defer close(done); server.handleConnection(left) }()
	_ = right.SetDeadline(time.Now().Add(3 * time.Second))
	for _, target := range []string{"rtsp://127.0.0.1/demo/hd", "rtsp://127.0.0.1/demo/sd?ai=passive"} {
		fmt.Fprintf(right, "DESCRIBE %s RTSP/1.0\r\nCSeq: 1\r\n\r\n", target)
		reader := bufio.NewReader(right)
		line, _ := reader.ReadString('\n')
		if !strings.Contains(line, "401 Unauthorized") {
			t.Fatalf("no challenge: %s", line)
		}
		challenge := false
		for {
			line, _ = reader.ReadString('\n')
			if line == "\r\n" {
				break
			}
			if strings.HasPrefix(line, "WWW-Authenticate: Basic") {
				challenge = true
			}
		}
		if !challenge {
			t.Fatal("missing VLC-compatible challenge")
		}
	}
	right.Close()
	<-done
	if len(server.streams) != 0 {
		t.Fatal("unauthorized camera was started")
	}
}
func TestRequestsCannotSwitchCameraQualityOrUseCredentialsInURL(t *testing.T) {
	client := &RTSPClient{cameraPath: "/Entrance", requestPath: "/Entrance/hd"}
	for _, path := range []string{"/Entrance/hd", "/Entrance/hd/video", "/Entrance/hd/audio"} {
		if !requestWithinCamera(client, &RTSPRequest{URL: "rtsp://127.0.0.1" + path}) {
			t.Fatal(path)
		}
	}
	for _, target := range []string{"rtsp://127.0.0.1/Other/hd/video", "rtsp://127.0.0.1/Entrance/sd", "rtsp://user:secret@127.0.0.1/Entrance/hd", "rtsp://127.0.0.1/Entrance/hd/../Other"} {
		if requestWithinCamera(client, &RTSPRequest{URL: target}) {
			t.Fatal("cross-session target accepted")
		}
	}
}
func TestRevokedActiveConnectionIsClosedWithoutAnotherClientRequest(t *testing.T) {
	a := fakeAuthorizer(t, func(w http.ResponseWriter, r *http.Request) {
		var data map[string]string
		_ = json.NewDecoder(r.Body).Decode(&data)
		if data["ticket"] != "ticket" || data["deviceId"] != "camera" {
			t.Error("session not scoped to device")
		}
		fmt.Fprint(w, `{}`)
	})
	left, right := net.Pipe()
	defer right.Close()
	stream := &CameraStream{camera: &storage.CameraInfo{DeviceID: "camera"}, clients: make(map[string]*RTSPClient)}
	client := &RTSPClient{session: "session", conn: left, stream: stream, authTicket: "ticket"}
	server := NewRTSPServer(0, nil)
	server.authorizer = a
	server.clients[client.session] = client
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { server.watchAuthorization(ctx, client); close(done) }()
	_ = right.SetReadDeadline(time.Now().Add(4 * time.Second))
	if _, err := right.Read(make([]byte, 1)); err == nil {
		t.Fatal("revoked connection survived")
	}
	<-done
}
func TestParserRejectsDuplicateAuthorizationAndOversizedLines(t *testing.T) {
	server := NewRTSPServer(0, nil)
	for _, raw := range []string{"OPTIONS * RTSP/1.0\r\nAuthorization: Basic a\r\nauthorization: Basic b\r\n\r\n", "OPTIONS " + strings.Repeat("x", 10000) + " RTSP/1.0\r\n\r\n"} {
		if _, err := server.parseRTSPRequestFromReader(bufio.NewReader(strings.NewReader(raw))); err == nil {
			t.Fatal("ambiguous request accepted")
		}
	}
}

func TestAuthorizedDeniedCameraNeverCreatesStreamOrWakes(t *testing.T) {
	// Isolated metadata; no real account, cloud connection or camera transport.
	directory := t.TempDir()
	previous, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Mkdir(filepath.Join(directory, ".tuya-data"), 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.Chdir(directory); err != nil {
		t.Fatal(err)
	}
	manager, err := storage.NewStorageManager()
	_ = os.Chdir(previous)
	if err != nil {
		t.Fatal(err)
	}
	if err = manager.SaveUser("eu", "synthetic@example.invalid", &tuya.SessionData{}); err != nil {
		t.Fatal(err)
	}
	users, err := manager.ListUsers()
	if err != nil || len(users) != 1 {
		t.Fatal("missing synthetic user")
	}
	if err = manager.SaveCameraRegistry(&storage.CameraRegistry{Cameras: []storage.CameraInfo{{UserKey: users[0].UserKey, DeviceID: "denied", DeviceName: "Demo", RTSPPath: "/Demo"}}}); err != nil {
		t.Fatal(err)
	}
	ticket := strings.Repeat("t", 32)
	a := fakeAuthorizer(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["deviceId"] == "denied" {
			fmt.Fprint(w, `{}`)
			return
		}
		if body["authorization"] != "Basic synthetic" && body["ticket"] != ticket {
			fmt.Fprint(w, `{}`)
			return
		}
		_ = json.NewEncoder(w).Encode(rtspGrant{Allowed: true, Ticket: ticket})
	})
	server := NewRTSPServer(0, manager)
	server.authorizer = a
	left, right := net.Pipe()
	defer right.Close()
	done := make(chan struct{})
	go func() { defer close(done); server.handleConnection(left) }()
	_ = right.SetDeadline(time.Now().Add(3 * time.Second))
	fmt.Fprint(right, "DESCRIBE rtsp://127.0.0.1/Demo/hd RTSP/1.0\r\nCSeq: 1\r\nAuthorization: Basic synthetic\r\n\r\n")
	line, _ := bufio.NewReader(right).ReadString('\n')
	if !strings.Contains(line, "403 Forbidden") {
		t.Fatal(line)
	}
	right.Close()
	<-done
	if len(server.streams) != 0 || len(server.clients) != 0 {
		t.Fatal("denied camera was woken")
	}
}

func TestReadOnlyInterleavedReportsCannotBecomeTalkAudio(t *testing.T) {
	// VLC sends interleaved receiver reports even during read-only playback.
	// Consume them without terminating playback or entering the talkback path.
	payload := []byte{'$', 4, 0, 4, 0, 0, 0, 0}
	client := &RTSPClient{reader: bufio.NewReader(bytes.NewReader(payload)), authAdmin: false, backAudioRTPChannel: 4}
	if err := (&RTSPServer{}).handleInterleavedRTP(client); err != nil {
		t.Fatal("read-only RTCP interrupted playback", err)
	}
	if remaining, _ := client.reader.Peek(1); len(remaining) != 0 {
		t.Fatal("packet not consumed")
	}
}

func TestFFmpegAuthenticatesWithBasicChallengeWithoutCredentialURLOnWire(t *testing.T) {
	executable, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Skip("FFmpeg not installed on this engine-test host")
	}
	directory := t.TempDir()
	previous, _ := os.Getwd()
	_ = os.Mkdir(filepath.Join(directory, ".tuya-data"), 0700)
	_ = os.Chdir(directory)
	manager, err := storage.NewStorageManager()
	_ = os.Chdir(previous)
	if err != nil {
		t.Fatal(err)
	}
	var authenticated atomic.Bool
	ticket := strings.Repeat("t", 32)
	a := fakeAuthorizer(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["authorization"] == "Basic "+base64.StdEncoding.EncodeToString([]byte("reader:synthetic-player-password")) {
			authenticated.Store(true)
		}
		if !authenticated.Load() {
			fmt.Fprint(w, `{}`)
			return
		}
		_ = json.NewEncoder(w).Encode(rtspGrant{Allowed: true, Ticket: ticket})
	})
	server := NewRTSPServer(0, manager)
	server.authorizer = a
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		conn, err := listener.Accept()
		if err == nil {
			server.handleConnection(conn)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	source := "rtsp://reader:synthetic-player-password@" + listener.Addr().String() + "/Unknown/sd"
	command := exec.CommandContext(ctx, executable, "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", source, "-t", "0.01", "-f", "null", "-")
	_, _ = command.CombinedOutput()
	_ = listener.Close()
	<-done
	if !authenticated.Load() {
		t.Fatal("FFmpeg could not authenticate against the VLC-compatible challenge")
	}
	if len(server.streams) != 0 {
		t.Fatal("unknown camera started a stream")
	}
}
