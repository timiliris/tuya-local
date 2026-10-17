package tuya

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	mqtt "github.com/eclipse/paho.mqtt.golang"
	"hash/crc32"
	"time"
	"tuya-ipc-terminal/pkg/core"
)

// The Tuya web player publishes CRC32(localKey) to m/w/<device>, QoS 1.
// This control message contains no video and does not change ICE routing.
func wakePayload(localKey string) []byte {
	payload := make([]byte, 4)
	binary.BigEndian.PutUint32(payload, crc32.ChecksumIEEE([]byte(localKey)))
	return payload
}

func wakeConfirmed(payload []byte) bool {
	var event struct {
		Protocol int `json:"protocol"`
		Data     struct {
			DPS map[string]json.RawMessage `json:"dps"`
		} `json:"data"`
	}
	if json.Unmarshal(payload, &event) != nil || event.Protocol != 4 {
		return false
	}
	value := string(event.Data.DPS["149"])
	return value == "true" || value == "1"
}

// wakeStatus also understands the boot events emitted by battery IPC firmware.
// lowPowerConnect=true means that only its low-power cloud connection is alive.
func wakeStatus(payload []byte, deviceID string) string {
	var event struct {
		Protocol int `json:"protocol"`
		Data     struct {
			DeviceID        string `json:"devId"`
			Online          *bool  `json:"online"`
			LowPowerConnect *bool  `json:"lowPowerConnect"`
		} `json:"data"`
	}
	if json.Unmarshal(payload, &event) != nil {
		return "ignored"
	}
	if event.Data.DeviceID != "" && event.Data.DeviceID != deviceID {
		return "ignored"
	}
	if wakeConfirmed(payload) {
		return "awake"
	}
	if event.Protocol != 1 || event.Data.DeviceID != deviceID {
		return "ignored"
	}
	if event.Data.Online != nil && !*event.Data.Online {
		return "offline"
	}
	if event.Data.LowPowerConnect != nil {
		if *event.Data.LowPowerConnect {
			return "sleeping"
		}
		if event.Data.Online != nil && *event.Data.Online {
			return "starting"
		}
	}
	if event.Data.Online != nil && *event.Data.Online {
		return "ready"
	}
	return "ignored"
}

func waitMQTT(ctx context.Context, token mqtt.Token) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-token.Done():
		return token.Error()
	}
}

func (c *MQTTClient) WakeUp(ctx context.Context, deviceID, parentID, localKey string) (bool, error) {
	retry := time.Second
	if c.mobileWake {
		retry = 6 * time.Second
	}
	return c.wakeUp(ctx, deviceID, parentID, localKey, retry)
}

func (c *MQTTClient) wakeUp(ctx context.Context, deviceID, parentID, localKey string, retryInterval time.Duration) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if deviceID == "" || localKey == "" {
		return false, errors.New("wake-up requires device and local key")
	}
	if parentID == "" {
		parentID = deviceID
	}
	topic := "smart/decrypt/in/" + parentID
	events := make(chan string, 32)
	requestedAt := time.Now().Unix()
	callback := func(_ mqtt.Client, message mqtt.Message) {
		if message.Retained() {
			return
		}
		var timestamp struct {
			T int64 `json:"t"`
		}
		if json.Unmarshal(message.Payload(), &timestamp) == nil && timestamp.T != 0 {
			if timestamp.T > 100000000000 {
				timestamp.T /= 1000
			}
			if timestamp.T < requestedAt-1 {
				return
			}
		}
		state := wakeStatus(message.Payload(), deviceID)
		if state == "ignored" {
			return
		}
		select {
		case events <- state:
		default:
		}
	}
	subscription := c.mqtt.Subscribe(topic, 1, callback)
	if err := waitMQTT(ctx, subscription); err != nil {
		return false, errors.New("wake-up status subscription failed")
	}
	defer c.mqtt.Unsubscribe(topic)
	if token, ok := subscription.(*mqtt.SubscribeToken); ok {
		core.Logger.Debug().Uint8("status", token.Result()[topic]).Msg("Wake status subscription acknowledgement")
		if token.Result()[topic] == 0x80 {
			return false, errors.New("wake status subscription not authorized")
		}
	}
	qos := byte(1)

	publish := func() error {
		return waitMQTT(ctx, c.mqtt.Publish("m/w/"+deviceID, qos, false, wakePayload(localKey)))
	}
	if err := publish(); err != nil {
		return false, errors.New("wake-up publish failed")
	}
	timeout := 40 * time.Second
	var retry <-chan time.Time
	if c.mobileWake {
		timeout = 5 * retryInterval
		ticker := time.NewTicker(retryInterval)
		defer ticker.Stop()
		retry = ticker.C
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	var settled <-chan time.Time
	var settleTimer *time.Timer
	defer func() {
		if settleTimer != nil {
			settleTimer.Stop()
		}
	}()
	for {
		select {
		case <-retry:
			if err := publish(); err != nil {
				return false, errors.New("wake-up retry failed")
			}
		case state := <-events:
			core.Logger.Info().Str("state", state).Msg("Camera wake status received")
			if settleTimer != nil {
				settleTimer.Stop()
				settled = nil
			}
			switch state {
			case "awake":
				return true, nil
			case "ready":
				if c.mobileWake {
					continue
				}
				// Online is a useful connection hint, not a confirmed video wake.
				return false, nil
			case "starting":
				if c.mobileWake {
					continue
				}
				// A waking camera can announce online before going offline for its boot.
				settleTimer = time.NewTimer(3 * time.Second)
				settled = settleTimer.C
			}
		case <-settled:
			return false, nil
		case <-timer.C:
			return false, nil
		case <-ctx.Done():
			return false, ctx.Err()
		}
	}
}

// RequestWakeUp follows the IPC SDK: publish wake, then request video immediately.
// Waiting for DP149 can let a battery camera return to sleep before an offer exists.
func (c *MQTTClient) RequestWakeUp(ctx context.Context, deviceID, localKey string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if deviceID == "" || localKey == "" {
		return errors.New("wake-up requires device and local key")
	}
	return waitMQTT(ctx, c.mqtt.Publish("m/w/"+deviceID, 1, false, wakePayload(localKey)))
}
