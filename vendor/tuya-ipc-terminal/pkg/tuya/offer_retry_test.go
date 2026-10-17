package tuya

import (
	"encoding/json"
	"testing"
)

func TestOfferRetransmissionPreservesSessionAndIncrementsRtx(t *testing.T) {
	mock := &wakeTestClient{}
	client := &MQTTClient{mqtt: mock, uid: "test-user"}
	camera := NewMqttCameraClient(client, &Device{DeviceId: "camera"}, &WebRTCConfig{})
	var first, second MqttMessage
	if err := camera.SendOffer("private-local-sdp", "hd", 1, false); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(mock.payload, &first); err != nil {
		t.Fatal(err)
	}
	if err := camera.ResendOffer("private-local-sdp", "hd", 1, false, 2); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(mock.payload, &second); err != nil {
		t.Fatal(err)
	}
	if first.Data.Header.SessionID != second.Data.Header.SessionID || first.Data.Header.Rtx != 0 || second.Data.Header.Rtx != 2 {
		t.Fatal("retransmission reset the session")
	}
	if string(first.Data.Message) != string(second.Data.Message) {
		t.Fatal("retransmission changed offer content")
	}
}
