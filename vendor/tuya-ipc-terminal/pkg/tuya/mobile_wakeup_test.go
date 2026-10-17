package tuya

import (
	"crypto/tls"
	"strings"
	"testing"
)

func TestMobileWakeMatchesAndroidAuthentication(t *testing.T) {
	login := &LoginResult{Uid: "example-user", Sid: "example-session", Ecode: "example-ecode"}
	login.Domain.MobileMqttsUrl = "m1.tuyaeu.com"
	options, err := mobileWakeOptions(login, "unique-bridge-device")
	if err != nil {
		t.Fatal(err)
	}
	if options.Username != login.Sid || options.Password != "e4fb63b3db68b021" {
		t.Fatal("mobile credentials differ from Android SDK derivation")
	}
	if options.Servers[0].String() != "tls://m1.tuyaeu.com:8883" {
		t.Fatal("mobile wake must use TLS broker")
	}
	if options.TLSConfig.InsecureSkipVerify || options.TLSConfig.MinVersion < tls.VersionTLS12 {
		t.Fatal("TLS certificate validation weakened")
	}
	if !strings.Contains(options.ClientID, "unique-bridge-device") || strings.Contains(options.ClientID, login.Sid) {
		t.Fatal("client identity must be unique and independent of the session credential")
	}
	if options.AutoReconnect {
		t.Fatal("one wake request must not create a permanent background retry loop")
	}
}

func TestMobileWakeRejectsIncompleteSessionsAndBrokerURLs(t *testing.T) {
	for _, login := range []*LoginResult{nil, {}, {Sid: "s", Uid: "u", Ecode: "e", Domain: Domain{MobileMqttsUrl: "https://unexpected.example/path"}}} {
		if _, err := mobileWakeOptions(login, "bridge-test"); err == nil {
			t.Fatal("invalid mobile wake configuration accepted")
		}
	}
}

func TestMobileWakePartnerAuthentication(t *testing.T) {
	login := &LoginResult{Uid: "example-user", Sid: "example-session", Ecode: "example-ecode", PartnerIdentity: "example-partner"}
	login.Domain.MobileMqttsUrl = "m1.tuyaeu.com"
	options, err := mobileWakeOptions(login, "unique-device")
	if err != nil {
		t.Fatal(err)
	}
	if options.Username != "example-partner_v1_3cxxt3au9x33ytvq3h9j_3f7060ea_mb_example-sessione746cddda28e9289" || options.Password != "f7afbd9bd2c9c2c4" {
		t.Fatal("partner authentication differs from public application vector")
	}
}
