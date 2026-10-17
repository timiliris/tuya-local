package rtsp

import (
	"net"
	"testing"
	"tuya-ipc-terminal/pkg/storage"
)

func TestObserverCannotWakeOrRetainStream(t *testing.T) {
	stream := &CameraStream{camera: &storage.CameraInfo{DeviceID: "camera"}, clients: make(map[string]*RTSPClient)}
	if stream.addObserver(&RTSPClient{session: "ai", passive: true}) {
		t.Fatal("observer woke inactive camera")
	}
	stream.active = true
	if stream.addObserver(&RTSPClient{session: "ai", passive: true}) {
		t.Fatal("observer attached without viewer")
	}
	reader, peer := net.Pipe()
	defer peer.Close()
	stream.clients["viewer"] = &RTSPClient{session: "viewer"}
	if !stream.addObserver(&RTSPClient{session: "ai", passive: true, conn: reader}) {
		t.Fatal("observer could not follow viewer")
	}
	stream.RemoveClient("viewer")
	if stream.hasViewer() {
		t.Fatal("observer owns stream")
	}
	if _, err := peer.Write([]byte("closed")); err == nil {
		t.Fatal("observer not closed with last viewer")
	}
}
func TestObserverFindsExistingQualityOnly(t *testing.T) {
	stream := &CameraStream{camera: &storage.CameraInfo{DeviceID: "camera"}, active: true, clients: map[string]*RTSPClient{"viewer": {}}}
	server := &RTSPServer{streams: map[string]*CameraStream{"camera-hd": stream}}
	if server.observedStream("camera") != stream {
		t.Fatal("HD viewer was not found")
	}
	if server.observedStream("other") != nil {
		t.Fatal("wrong camera exposed")
	}
	delete(stream.clients, "viewer")
	if server.observedStream("camera") != nil {
		t.Fatal("idle session exposed")
	}
}
