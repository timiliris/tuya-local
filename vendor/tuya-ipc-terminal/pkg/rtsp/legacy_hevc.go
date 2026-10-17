package rtsp

import (
	"encoding/binary"
	"github.com/pion/rtp"
)

// Some Tuya cameras send HEVC NAL units using H264 STAP-A/FU-A envelopes.
// Translate their envelopes to RFC 7798 without decoding the video.
type legacyHEVCPacketizer struct {
	parameters bool
	active     bool
	header     [2]byte
	sequence   uint16
	timestamp  uint32
}

func (n *legacyHEVCPacketizer) convert(packet *rtp.Packet) *rtp.Packet {
	p := packet.Payload
	if len(p) < 2 {
		return nil
	}
	var payload []byte
	switch p[0] & 31 {
	case 24:
		offset := 1
		parameters := n.parameters
		for offset < len(p) {
			if offset+2 > len(p) {
				return nil
			}
			size := int(binary.BigEndian.Uint16(p[offset : offset+2]))
			offset += 2
			if size < 2 || offset+size > len(p) || !hevcNALHeader(p[offset], p[offset+1]) {
				return nil
			}
			if kind := (p[offset] >> 1) & 63; kind >= 32 && kind <= 34 {
				parameters = true
			}
			offset += size
		}
		if len(p) < 5 {
			return nil
		}
		n.parameters = parameters
		payload = append([]byte{(p[3] & 0x81) | 96, p[4]}, p[1:]...)
	case 28:
		start := p[1]&128 != 0
		if start {
			if len(p) < 4 || !hevcNALHeader((p[0]&224)|(p[1]&31), p[2]) {
				n.active = false
				return nil
			}
			n.header = [2]byte{(p[0] & 224) | (p[1] & 31), p[2]}
			n.timestamp = packet.Timestamp
			n.active = true
		} else if !n.active || n.sequence+1 != packet.SequenceNumber || n.timestamp != packet.Timestamp {
			n.active = false
			return nil
		}
		n.sequence = packet.SequenceNumber
		offset := 2
		if start {
			offset = 3
		}
		payload = append([]byte{(n.header[0] & 0x81) | 98, n.header[1], (p[1] & 192) | ((n.header[0] >> 1) & 63)}, p[offset:]...)
		if p[1]&64 != 0 {
			n.active = false
		}
	default:
		kind := (p[0] >> 1) & 63
		if kind == 48 && p[0]&0x81 == 0 && p[1]&0xf8 == 0 && p[1]&7 != 0 {
			offset, parameters := 2, n.parameters
			for offset < len(p) {
				if offset+2 > len(p) {
					return nil
				}
				size := int(binary.BigEndian.Uint16(p[offset : offset+2]))
				offset += 2
				if size < 2 || offset+size > len(p) || !hevcNALHeader(p[offset], p[offset+1]) {
					return nil
				}
				if k := (p[offset] >> 1) & 63; k >= 32 && k <= 34 {
					parameters = true
				}
				offset += size
			}
			n.parameters = parameters
			return packet
		}
		if kind == 49 && len(p) >= 4 && p[0]&0x81 == 0 && p[1]&0xf8 == 0 && p[1]&7 != 0 {
			return packet
		}
		if !hevcNALHeader(p[0], p[1]) {
			return nil
		}
		if kind := (p[0] >> 1) & 63; kind >= 32 && kind <= 34 {
			n.parameters = true
		}
		return packet
	}
	out := packet.Clone()
	out.Payload = payload
	return out
}

// This IPC uses single-layer HEVC. Initial H264 SD packets must not be
// mistaken for a valid HD stream before its VPS/SPS/PPS arrives.
func hevcNALHeader(first, second byte) bool {
	return first&0x81 == 0 && second&0xf8 == 0 && second&7 != 0 && (first>>1)&63 < 48
}
