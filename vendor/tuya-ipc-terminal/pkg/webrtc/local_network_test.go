package webrtc

import (
	"strings"
	"testing"
	"time"

	pion "github.com/pion/webrtc/v4"
)

func TestDockerAdvertisesReachableHostCandidate(t *testing.T) {
	t.Setenv("TUYA_WEBRTC_HOST_IP", "192.168.50.10")
	api, err := NewAPI()
	if err != nil {
		t.Fatal(err)
	}
	pc, err := api.NewPeerConnection(pion.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	if _, err = pc.CreateDataChannel("test", nil); err != nil {
		t.Fatal(err)
	}
	done := pion.GatheringCompletePromise(pc)
	offer, err := pc.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = pc.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("candidate gathering timed out")
	}
	sdp := pc.LocalDescription().SDP
	found := false
	for _, line := range strings.Split(sdp, "\n") {
		if !strings.HasPrefix(line, "a=candidate:") || !strings.Contains(line, "typ host") {
			continue
		}
		fields := strings.Fields(line)
		if fields[4] != "192.168.50.10" {
			t.Fatalf("unreachable host candidate: %s", line)
		}
		if fields[2] == "udp" && (fields[5] < "52000" || fields[5] > "52019") {
			t.Fatalf("unmapped port: %s", line)
		}
		found = true
	}
	if !found {
		t.Fatal("no reachable host candidate")
	}
}

func TestDockerRejectsInvalidHostAddress(t *testing.T) {
	t.Setenv("TUYA_WEBRTC_HOST_IP", "invalid")
	if _, err := NewAPI(); err == nil {
		t.Fatal("invalid IP accepted")
	}
}
