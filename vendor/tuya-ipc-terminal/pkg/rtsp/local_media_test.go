package rtsp

import (
	"strings"
	"testing"
	"tuya-ipc-terminal/pkg/storage"
	"tuya-ipc-terminal/pkg/tuya"
)

func TestLocalMediaRejectsInternetAndRelayCandidates(t *testing.T) {
	for _, tt := range []struct {
		candidate string
		allowed   bool
	}{
		{"candidate:1 1 udp 100 192.168.50.20 5000 typ host", true},
		{"a=candidate:1 1 udp 100 10.1.1.2 5000 typ host\r\n", true},
		{"candidate:1 1 udp 100 fd00::1 5000 typ host", true},
		{"candidate:1 1 udp 100 8.8.8.8 5000 typ host", false},
		{"candidate:1 1 udp 100 192.168.50.20 5000 typ relay", false},
		{"candidate:1 1 udp 100 127.0.0.1 5000 typ host", false},
		{"candidate:1 1 udp 100 unknown.local 5000 typ host", false},
		{"invalid", false},
	} {
		if got := localCandidate(tt.candidate); got != tt.allowed {
			t.Errorf("%s: got %v", tt.candidate, got)
		}
	}
}

func TestLocalSDPRemovesPublicAndRelayCandidates(t *testing.T) {
	source := "v=0\r\na=candidate:1 1 udp 100 192.168.50.20 5000 typ host\r\na=candidate:2 1 udp 90 8.8.8.8 6000 typ srflx\r\na=candidate:3 1 udp 80 10.0.0.1 6000 typ relay\r\n"
	got := localSDP(source)
	if !strings.Contains(got, "192.168.50.20") || strings.Contains(got, "8.8.8.8") || strings.Contains(got, "typ relay") {
		t.Fatalf("incorrect local SDP: %s", got)
	}
	if !strings.HasPrefix(got, "v=0\r\n") {
		t.Fatal("SDP header lost")
	}
}

func TestLocalMediaDoesNotAllocatePublicSTUNOrTURN(t *testing.T) {
	t.Setenv("TUYA_LOCAL_MEDIA_ONLY", "1")
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	defer bridge.Stop()
	config := &tuya.WebRTCConfig{P2PConfig: tuya.P2PConfig{Ices: []tuya.ICEServer{{Urls: "stun:example.invalid:3478"}}}}
	if err := bridge.setupPeerConnection(config); err != nil {
		t.Fatal(err)
	}
	if len(bridge.peerConnection.GetConfiguration().ICEServers) != 0 {
		t.Fatal("local video allocated a public ICE transport")
	}
	if len(config.P2PConfig.Ices) != 1 {
		t.Fatal("camera control configuration was modified")
	}
}
