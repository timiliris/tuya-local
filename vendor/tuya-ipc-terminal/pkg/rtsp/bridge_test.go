package rtsp

import (
	"testing"
	"tuya-ipc-terminal/pkg/storage"
)

func TestStopCleansUpUnfinishedConnection(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	bridge.Stop()
	select {
	case <-bridge.ctx.Done():
	default:
		t.Fatal("unfinished connection was not cancelled")
	}
}

func TestHEVCTransportSelection(t *testing.T) {
	bridge := &WebRTCBridge{isHEVC: true}
	t.Setenv("TUYA_HEVC_TRANSPORT", "rtp")
	if bridge.usesDataChannel() {
		t.Fatal("RTP mode incorrectly opened a data channel")
	}
	t.Setenv("TUYA_HEVC_TRANSPORT", "")
	if !bridge.usesDataChannel() {
		t.Fatal("default HEVC transport changed")
	}
	bridge.isHEVC = false
	if bridge.usesDataChannel() {
		t.Fatal("H264 should use RTP")
	}
}
