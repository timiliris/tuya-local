package tuya

import (
	"bytes"
	"context"
	"errors"
	mqtt "github.com/eclipse/paho.mqtt.golang"
	"testing"
	"time"
)

type wakeTestToken struct{ err error }

func (t wakeTestToken) Wait() bool                     { return true }
func (t wakeTestToken) WaitTimeout(time.Duration) bool { return true }
func (t wakeTestToken) Done() <-chan struct{}          { c := make(chan struct{}); close(c); return c }
func (t wakeTestToken) Error() error                   { return t.err }

type wakeTestMessage struct {
	mqtt.Message
	data []byte
}

func (m wakeTestMessage) Payload() []byte { return m.data }
func (m wakeTestMessage) Retained() bool  { return false }

type wakeTestClient struct {
	mqtt.Client
	callback                              mqtt.MessageHandler
	subscription, published, unsubscribed string
	qos                                   byte
	retained                              bool
	payload                               []byte
	failure                               error
	publishCount, acknowledgeAfter        int
	events                                []string
}

func (m *wakeTestClient) Subscribe(topic string, qos byte, callback mqtt.MessageHandler) mqtt.Token {
	m.subscription = topic
	m.callback = callback
	return wakeTestToken{}
}
func (m *wakeTestClient) Publish(topic string, qos byte, retained bool, payload interface{}) mqtt.Token {
	m.publishCount++
	m.published = topic
	m.qos = qos
	m.retained = retained
	m.payload = payload.([]byte)
	if m.callback != nil && m.failure == nil && (m.acknowledgeAfter == 0 || m.publishCount >= m.acknowledgeAfter) {
		if m.events == nil {
			m.callback(m, wakeTestMessage{data: []byte(`{"protocol":4,"data":{"dps":{"149":true}}}`)})
		} else {
			for _, event := range m.events {
				m.callback(m, wakeTestMessage{data: []byte(event)})
			}
		}
	}
	return wakeTestToken{m.failure}
}
func (m *wakeTestClient) Unsubscribe(topics ...string) mqtt.Token {
	m.unsubscribed = topics[0]
	return wakeTestToken{}
}

func TestWakeUpMatchesTuyaWebPayloadAndAcknowledgement(t *testing.T) {
	mock := &wakeTestClient{}
	c := &MQTTClient{mqtt: mock}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	confirmed, err := c.WakeUp(ctx, "camera", "", "123456789")
	if err != nil || !confirmed {
		t.Fatalf("not confirmed: %v", err)
	}
	if mock.published != "m/w/camera" || mock.subscription != "smart/decrypt/in/camera" || mock.qos != 1 || mock.retained {
		t.Fatal("incorrect MQTT request")
	}
	if !bytes.Equal(mock.payload, []byte{0xcb, 0xf4, 0x39, 0x26}) {
		t.Fatalf("incorrect CRC bytes: %x", mock.payload)
	}
	if mock.unsubscribed != mock.subscription {
		t.Fatal("status subscription leaked")
	}
}

func TestWakeUpFailureAndParentSubscription(t *testing.T) {
	mock := &wakeTestClient{failure: errors.New("broker rejected")}
	c := &MQTTClient{mqtt: mock}
	_, err := c.WakeUp(context.Background(), "camera", "gateway", "123456789")
	if err == nil || mock.subscription != "smart/decrypt/in/gateway" || mock.unsubscribed != mock.subscription {
		t.Fatal("failure not handled")
	}
	if _, err = c.WakeUp(context.Background(), "camera", "", ""); err == nil {
		t.Fatal("missing local key accepted")
	}
}

func TestWakeUpStatusDoesNotConfuseOtherMessages(t *testing.T) {
	for _, payload := range []string{`{}`, `{"protocol":4,"data":{"dps":{"149":false}}}`, `{"protocol":302,"data":{"dps":{"149":true}}}`, `bad`} {
		if wakeConfirmed([]byte(payload)) {
			t.Fatal("false wake confirmation")
		}
	}
	if !wakeConfirmed([]byte(`{"protocol":4,"data":{"dps":{"149":1}}}`)) {
		t.Fatal("numeric wake status ignored")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	token := wakeTestPendingToken{}
	if !errors.Is(waitMQTT(ctx, token), context.Canceled) {
		t.Fatal("cancellation ignored")
	}
}

type wakeTestPendingToken struct{ wakeTestToken }

func (wakeTestPendingToken) Done() <-chan struct{} { return make(chan struct{}) }

func TestWakeStatusRecognizesBatteryBootAndSleep(t *testing.T) {
	cases := []struct{ payload, want string }{
		{`{"protocol":1,"data":{"devId":"camera","online":true,"lowPowerConnect":false}}`, "starting"},
		{`{"protocol":1,"data":{"devId":"camera","online":false}}`, "offline"},
		{`{"protocol":1,"data":{"devId":"camera","online":true}}`, "ready"},
		{`{"protocol":1,"data":{"devId":"camera","online":true,"lowPowerConnect":true}}`, "sleeping"},
		{`{"protocol":1,"data":{"devId":"other","online":true}}`, "ignored"},
		{`{"protocol":4,"data":{"devId":"other","dps":{"149":true}}}`, "ignored"},
		{`{"protocol":1,"data":{"online":true}}`, "ignored"},
	}
	for _, c := range cases {
		if got := wakeStatus([]byte(c.payload), "camera"); got != c.want {
			t.Fatalf("%s: got %s, want %s", c.payload, got, c.want)
		}
	}
}

func TestWakeUpWaitsForBatteryBootSequence(t *testing.T) {
	mock := &wakeTestClient{events: []string{
		`{"protocol":1,"data":{"devId":"camera","online":true,"lowPowerConnect":false}}`,
		`{"protocol":1,"data":{"devId":"camera","online":false}}`,
		`{"protocol":1,"data":{"devId":"other","online":true}}`,
		`{"protocol":1,"data":{"devId":"camera","online":true}}`,
	}}
	confirmed, err := (&MQTTClient{mqtt: mock}).WakeUp(context.Background(), "camera", "", "123456789")
	if err != nil || confirmed {
		t.Fatalf("online event should only release the wait, not confirm video wake: %v", err)
	}
}

func TestOfflineEventCancelsPrematureWakeConfirmation(t *testing.T) {
	mock := &wakeTestClient{events: []string{
		`{"protocol":1,"data":{"devId":"camera","online":true,"lowPowerConnect":false}}`,
		`{"protocol":1,"data":{"devId":"camera","online":false}}`,
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 3100*time.Millisecond)
	defer cancel()
	confirmed, err := (&MQTTClient{mqtt: mock}).WakeUp(ctx, "camera", "", "123456789")
	if confirmed || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("offline boot was treated as ready: %v", err)
	}
}

func TestMobileWakeRetriesUntilAcknowledged(t *testing.T) {
	mock := &wakeTestClient{acknowledgeAfter: 3}
	confirmed, err := (&MQTTClient{mqtt: mock, mobileWake: true}).wakeUp(context.Background(), "camera", "", "123456789", time.Millisecond)
	if err != nil || !confirmed || mock.publishCount != 3 || mock.qos != 1 || mock.retained {
		t.Fatalf("mobile wake retries failed: count=%d qos=%d error=%v", mock.publishCount, mock.qos, err)
	}
}

func TestMobileWakeRetriesAreBoundedAndCancelCleanly(t *testing.T) {
	for _, cancelled := range []bool{false, true} {
		mock := &wakeTestClient{events: []string{}}
		ctx, cancel := context.WithCancel(context.Background())
		if cancelled {
			cancel()
		}
		_, err := (&MQTTClient{mqtt: mock, mobileWake: true}).wakeUp(ctx, "camera", "", "123456789", time.Millisecond)
		cancel()
		if cancelled && !errors.Is(err, context.Canceled) {
			t.Fatal("cancellation ignored")
		}
		if !cancelled && (err != nil || mock.publishCount < 2 || mock.publishCount > 6) {
			t.Fatalf("unbounded retries: %d, %v", mock.publishCount, err)
		}
		if mock.unsubscribed != mock.subscription {
			t.Fatal("subscription leaked")
		}
	}
}

func TestMobileWakeDoesNotTreatOnlineHintAsReadyForVideo(t *testing.T) {
	mock := &wakeTestClient{events: []string{`{"protocol":1,"data":{"devId":"camera","online":true}}`}}
	confirmed, err := (&MQTTClient{mqtt: mock, mobileWake: true}).wakeUp(context.Background(), "camera", "", "key", time.Millisecond)
	if err != nil || confirmed || mock.publishCount < 3 {
		t.Fatalf("online hint stopped battery wake too early: count=%d err=%v", mock.publishCount, err)
	}
}

func TestMobileWakeIgnoresOldAwakeNotifications(t *testing.T) {
	mock := &wakeTestClient{events: []string{`{"protocol":4,"t":1700000000,"data":{"devId":"camera","dps":{"149":true}}}`}}
	confirmed, err := (&MQTTClient{mqtt: mock, mobileWake: true}).wakeUp(context.Background(), "camera", "", "key", time.Millisecond)
	if err != nil || confirmed || mock.publishCount < 3 {
		t.Fatal("old awake state was accepted")
	}
}

func TestIPCWakeRequestsVideoWithoutWaitingForStatus(t *testing.T) {
	mock := &wakeTestClient{}
	c := &MQTTClient{mqtt: mock}
	if err := c.RequestWakeUp(context.Background(), "camera", "123456789"); err != nil {
		t.Fatal(err)
	}
	if mock.publishCount != 1 || mock.qos != 1 || mock.retained || mock.subscription != "" || !bytes.Equal(mock.payload, []byte{0xcb, 0xf4, 0x39, 0x26}) {
		t.Fatal("IPC wake is not a single immediate control request")
	}
}
