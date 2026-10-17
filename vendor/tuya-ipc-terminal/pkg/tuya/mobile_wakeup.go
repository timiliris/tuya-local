package tuya

import (
	"context"
	"crypto/hmac"
	"crypto/md5"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"net"
	"strings"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"tuya-ipc-terminal/pkg/utils"
)

// Android IPC SDK 7.8.x publishes wirelessWake through the mobile TLS broker.
// Its normal-account MQTT username is the existing SID; its password is the
// middle 16 hexadecimal characters of MD5(ecode). No video uses this client.
func mobileWakeOptions(login *LoginResult, deviceIdentity string) (*mqtt.ClientOptions, error) {
	if login == nil || login.Sid == "" || login.Ecode == "" || login.Uid == "" || deviceIdentity == "" {
		return nil, errors.New("mobile wake session is incomplete")
	}
	host := login.Domain.MobileMqttsUrl
	if host == "" || strings.ContainsAny(host, "/:@?#") {
		return nil, errors.New("invalid mobile wake broker")
	}
	uidHash := md5.Sum([]byte(login.Uid + "sdkfasodifca"))
	ecodeHash := md5.Sum([]byte(login.Ecode))
	clientID := "com.tuya.smart_mb_" + deviceIdentity + "_" + hex.EncodeToString(uidHash[:]) + "_DEFAULT"
	options := mqtt.NewClientOptions().AddBroker("tls://" + net.JoinHostPort(host, "8883"))
	options.SetClientID(clientID)
	options.SetUsername(login.Sid)
	options.SetPassword(hex.EncodeToString(ecodeHash[:])[8:24])
	if login.PartnerIdentity != "" {
		const appID = "3cxxt3au9x33ytvq3h9j"
		const certificate = "com.tuya.smart_93:21:9F:C2:73:E2:20:0F:4A:DE:E5:F7:19:1D:C6:56:BA:2A:2D:7B:2F:F5:D2:4C:D5:5C:4B:61:55:00:1E:40"
		const global = certificate + "_f3hd7pet4p83kemjdf5wqsa5tavrv579_5gdtanjtf38vyxkqh87cjwfcqjhvjjqa"
		md := func(value string) string { sum := md5.Sum([]byte(value)); return hex.EncodeToString(sum[:]) }
		channel := hmac.New(sha256.New, []byte(appID))
		channel.Write([]byte(certificate))
		channelKey := hex.EncodeToString(channel.Sum(nil))[8:16]
		options.SetUsername(login.PartnerIdentity + "_v1_" + appID + "_" + channelKey + "_mb_" + login.Sid + md(md(appID) + login.Ecode)[16:])
		options.SetPassword(md(md(global) + login.Ecode)[8:24])
	}
	options.SetTLSConfig(&tls.Config{MinVersion: tls.VersionTLS12})
	options.SetCleanSession(true)
	options.SetAutoReconnect(false)
	options.SetConnectTimeout(8 * time.Second)
	options.SetKeepAlive(60 * time.Second)
	return options, nil
}

func NewMobileWakeClient(ctx context.Context, login *LoginResult) (*MQTTClient, error) {
	options, err := mobileWakeOptions(login, "bridge"+utils.RandString(24, 62))
	if err != nil {
		return nil, err
	}
	client := &MQTTClient{uid: login.Uid, mobileWake: true}
	if _, err := rand.Read(client.signalSource[:]); err != nil {
		return nil, errors.New("mobile signaling identity unavailable")
	}
	client.mqtt = mqtt.NewClient(options)
	if err := waitMQTT(ctx, client.mqtt.Connect()); err != nil {
		client.mqtt.Disconnect(0)
		return nil, errors.New("mobile wake broker connection failed")
	}
	return client, nil
}

func (c *MQTTClient) EnableMobileWebRTC(ctx context.Context) error {
	topic := "/av/u/" + c.uid
	token := c.mqtt.Subscribe(topic, 1, c.consume)
	if err := waitMQTT(ctx, token); err != nil {
		return err
	}
	if subscription, ok := token.(*mqtt.SubscribeToken); ok && subscription.Result()[topic] == 0x80 {
		return errors.New("mobile WebRTC subscription refused")
	}
	return nil
}
