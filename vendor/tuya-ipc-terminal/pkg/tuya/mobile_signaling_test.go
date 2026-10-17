package tuya

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	mqtt "github.com/eclipse/paho.mqtt.golang"
	"strings"
	"testing"
)

func TestMobileSignalingMatchesIndependentProtocolVector(t *testing.T) {
	// Public interoperability vector from roquerodrigo/tuya-ipc-p2p-sdk,
	// tests/test_signaling.py (MIT); no account or camera data.
	key := []byte("0123456789abcdef")
	plain := []byte(`{"protocol":302,"pv":"2.2","t":1700000000,"data":{"header":{"type":"offer"},"msg":{}}}`)
	want := "322e322841b2050000000400000001ea755a246e67a8cc3e61dbdaf73c5ff5d16b70eb9fdecab88f58ebe333fd3bc99b0b853bab143cbd29db21f1dcb1baccbe454130944da5bb3dac257dab4987acf038d9e2020dd6a986d7561eef5e9de31e60787eb6d61f55b35238f5cee5f7c7"
	frame, err := encodeMobileFrame(key, 4, [4]byte{0, 0, 0, 1}, plain)
	if err != nil || hex.EncodeToString(frame) != want {
		t.Fatal("native control envelope differs from interoperability vector")
	}
	got, err := decodeMobileFrame(key, frame)
	if err != nil || !bytes.Equal(got, plain) {
		t.Fatal("native control frame failed to decode")
	}
	frame[len(frame)-1] ^= 1
	if _, err = decodeMobileFrame(key, frame); err == nil {
		t.Fatal("corrupted control message accepted")
	}
	if _, err = decodeMobileFrame(key, []byte("bad")); err == nil {
		t.Fatal("truncated message accepted")
	}
	if _, err = encodeMobileFrame([]byte("bad"), 1, [4]byte{}, plain); err == nil {
		t.Fatal("invalid key accepted")
	}
}

type mobilePublishCapture struct {
	mqtt.Client
	topic   string
	payload []byte
}

func (m *mobilePublishCapture) Publish(topic string, qos byte, retained bool, payload interface{}) mqtt.Token {
	m.topic = topic
	m.payload = payload.([]byte)
	return wakeTestToken{}
}

func TestMobileOfferKeepsWebRTCVideoAndUsesDirectControlTopic(t *testing.T) {
	capture := &mobilePublishCapture{}
	broker := &MQTTClient{mqtt: capture, uid: "example-user", signalKey: []byte("0123456789abcdef")}
	camera := NewMqttCameraClient(broker, &Device{DeviceId: "example-camera"}, &WebRTCConfig{Auth: "example-auth", MotoId: "web-route", Id: "example-session-id"})
	if err := camera.SendOffer("v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n", "sd", 4, false); err != nil {
		t.Fatal(err)
	}
	if len(camera.SessionId) != 32 || camera.SessionId == "example-session-id" {
		t.Fatal("camera session must be unique")
	}
	if capture.topic != "smart/mb/out/example-camera" {
		t.Fatal("offer used web relay control topic")
	}
	plain, err := decodeMobileFrame(broker.signalKey, capture.payload)
	if err != nil {
		t.Fatal(err)
	}
	var frame MqttMessage
	if err = json.Unmarshal(plain, &frame); err != nil {
		t.Fatal(err)
	}
	if frame.T > 10000000000 || frame.Data.Header.From != "example-user" || frame.Data.Header.Path != "mqtt" || frame.Data.Header.MotoID != "" {
		t.Fatal("incorrect mobile control header")
	}
	var offer OfferFrame
	if err = json.Unmarshal(frame.Data.Message, &offer); err != nil {
		t.Fatal(err)
	}
	if offer.Mode != "webrtc" || !strings.Contains(offer.Sdp, "UDP/TLS/RTP/SAVPF") || offer.DatachannelEnable {
		t.Fatal("native signaling changed the local WebRTC media transport")
	}
}

func TestMobileGCMAuthenticatesHeaderAndBody(t *testing.T) {
	key := []byte("0123456789abcdef")
	plain := []byte(`{"protocol":302,"data":{}}`)
	frame, err := encodeMobileGCM(key, 1, [4]byte{0, 0, 0, 1}, plain)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := decodeMobileFrame(key, frame)
	if err != nil || !bytes.Equal(decoded, plain) {
		t.Fatal("GCM decode failed")
	}
	for _, offset := range []int{3, 12, 24, len(frame) - 1} {
		corrupt := append([]byte{}, frame...)
		corrupt[offset] ^= 1
		if _, err := decodeMobileFrame(key, corrupt); err == nil {
			t.Fatal("unauthenticated control accepted")
		}
	}
}
