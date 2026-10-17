package tuya

import "testing"

func TestMqttStreamTypeMapsMainAndSubRegardlessOfCodec(t *testing.T) {
	for _, tt := range []struct{ input, output int }{{2, 0}, {4, 1}, {0, 0}, {1, 1}, {7, 7}} {
		if got := mqttStreamType(tt.input); got != tt.output {
			t.Errorf("stream %d: got %d, want %d", tt.input, got, tt.output)
		}
	}
}
