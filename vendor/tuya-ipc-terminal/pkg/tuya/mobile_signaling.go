package tuya

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"sync/atomic"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

// Mobile MQTT uses a 2.2 CRC envelope around AES-ECB/PKCS7 control messages.
// This is control traffic only; the peer connection still enforces local ICE.
func encodeMobileFrame(key []byte, sequence uint32, source [4]byte, plain []byte) ([]byte, error) {
	if len(key) != 16 {
		return nil, errors.New("invalid mobile signaling key")
	}
	block, _ := aes.NewCipher(key)
	padding := aes.BlockSize - len(plain)%aes.BlockSize
	padded := append(append([]byte{}, plain...), bytes.Repeat([]byte{byte(padding)}, padding)...)
	frame := make([]byte, 15+len(padded))
	copy(frame, "2.2")
	binary.BigEndian.PutUint32(frame[7:11], sequence)
	copy(frame[11:15], source[:])
	for offset := 0; offset < len(padded); offset += aes.BlockSize {
		block.Encrypt(frame[15+offset:15+offset+aes.BlockSize], padded[offset:offset+aes.BlockSize])
	}
	binary.BigEndian.PutUint32(frame[3:7], crc32.ChecksumIEEE(frame[7:]))
	return frame, nil
}

// TuyaOpen tuya_protocol.c documents MQTT 2.3: 12-byte authenticated header,
// 12-byte nonce, ciphertext and a 16-byte GCM authentication tag.
func encodeMobileGCM(key []byte, sequence uint32, source [4]byte, plain []byte) ([]byte, error) {
	if len(key) != 16 {
		return nil, errors.New("invalid mobile signaling key")
	}
	block, _ := aes.NewCipher(key)
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	frame := make([]byte, 24)
	copy(frame, "2.3")
	binary.BigEndian.PutUint32(frame[3:7], sequence)
	copy(frame[7:11], source[:])
	if _, err := rand.Read(frame[12:24]); err != nil {
		return nil, err
	}
	return gcm.Seal(frame, frame[12:24], plain, frame[:12]), nil
}

func decodeMobileFrame(key, frame []byte) ([]byte, error) {
	if len(key) == 16 && len(frame) >= 40 && string(frame[:3]) == "2.3" {
		block, _ := aes.NewCipher(key)
		gcm, err := cipher.NewGCM(block)
		if err != nil {
			return nil, err
		}
		return gcm.Open(nil, frame[12:24], frame[24:], frame[:12])
	}
	if len(key) != 16 || len(frame) < 31 || string(frame[:3]) != "2.2" || (len(frame)-15)%aes.BlockSize != 0 {
		return nil, errors.New("invalid mobile signaling frame")
	}
	if binary.BigEndian.Uint32(frame[3:7]) != crc32.ChecksumIEEE(frame[7:]) {
		return nil, errors.New("corrupted mobile signaling frame")
	}
	block, _ := aes.NewCipher(key)
	plain := make([]byte, len(frame)-15)
	for offset := 0; offset < len(plain); offset += aes.BlockSize {
		block.Decrypt(plain[offset:offset+aes.BlockSize], frame[15+offset:15+offset+aes.BlockSize])
	}
	n := int(plain[len(plain)-1])
	if n < 1 || n > aes.BlockSize || !bytes.Equal(plain[len(plain)-n:], bytes.Repeat([]byte{byte(n)}, n)) {
		return nil, errors.New("invalid mobile signaling padding")
	}
	return plain[:len(plain)-n], nil
}

func (c *MQTTClient) EnableMobileSignaling(ctx context.Context, deviceID, localKey string) error {
	if deviceID == "" || len(localKey) != 16 {
		return errors.New("mobile signaling requires camera and key")
	}
	c.signalKey = []byte(localKey)
	token := c.mqtt.Subscribe("smart/mb/in/"+deviceID, 1, c.consume)
	if err := waitMQTT(ctx, token); err != nil {
		return errors.New("mobile signaling subscription failed")
	}
	if subscription, ok := token.(*mqtt.SubscribeToken); ok && subscription.Result()["smart/mb/in/"+deviceID] == 0x80 {
		return errors.New("mobile signaling subscription refused")
	}
	return nil
}

func (c *MQTTClient) cameraPayload(payload []byte) ([]byte, error) {
	if len(c.signalKey) == 0 {
		return payload, nil
	}
	return encodeMobileGCM(c.signalKey, atomic.AddUint32(&c.signalSequence, 1), c.signalSource, payload)
}
