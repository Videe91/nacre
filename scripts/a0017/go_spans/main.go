// A-0017 evaluation helper: gitleaks' own engine (Go regexp) as ground truth.
// Reads rules.json ([{id, regex, docs}]) and corpus.json ([doc]); for every rule and document it
// prints sha256 of the NUL-joined FindAllString matches. Run: go run . rules.json corpus.json > go.json
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"regexp"
	"strings"
)

type rule struct {
	ID    string   `json:"id"`
	Regex string   `json:"regex"`
	Docs  []string `json:"docs"`
}

func main() {
	var rules []rule
	var corpus []string
	must(json.Unmarshal(read(os.Args[1]), &rules))
	must(json.Unmarshal(read(os.Args[2]), &corpus))
	out := map[string][]string{}
	for _, r := range rules {
		re, err := regexp.Compile(r.Regex)
		if err != nil {
			out[r.ID] = []string{"COMPILE_ERROR: " + err.Error()}
			continue
		}
		for _, doc := range append(append([]string{}, corpus...), r.Docs...) {
			sum := sha256.Sum256([]byte(strings.Join(re.FindAllString(doc, -1), "\x00")))
			out[r.ID] = append(out[r.ID], hex.EncodeToString(sum[:]))
		}
	}
	must(json.NewEncoder(os.Stdout).Encode(out))
}

func read(p string) []byte { b, err := os.ReadFile(p); must(err); return b }
func must(err error) {
	if err != nil {
		panic(err)
	}
}
