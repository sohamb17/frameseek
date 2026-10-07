package store

import (
	"errors"
	"testing"
)

func TestValidateEvalQuery(t *testing.T) {
	ans := []EvalAnswer{{VideoID: "v", StartMs: 1000, EndMs: 5000}}
	cases := []struct {
		name string
		q    NewEvalQuery
		ok   bool
	}{
		{"speech with answer", NewEvalQuery{Query: "q", QueryType: "speech", Answers: ans}, true},
		{"speech without answer", NewEvalQuery{Query: "q", QueryType: "speech"}, false},
		{"no_answer empty", NewEvalQuery{Query: "q", QueryType: "no_answer"}, true},
		{"no_answer with answer", NewEvalQuery{Query: "q", QueryType: "no_answer", Answers: ans}, false},
		{"reversed interval", NewEvalQuery{Query: "q", QueryType: "visual", Answers: []EvalAnswer{{VideoID: "v", StartMs: 5000, EndMs: 1000}}}, false},
		{"negative start", NewEvalQuery{Query: "q", QueryType: "ocr", Answers: []EvalAnswer{{VideoID: "v", StartMs: -1, EndMs: 1000}}}, false},
	}
	for _, c := range cases {
		err := validateEvalQuery(c.q)
		if c.ok && err != nil {
			t.Errorf("%s: unexpected error %v", c.name, err)
		}
		if !c.ok && !errors.Is(err, ErrInvalid) {
			t.Errorf("%s: want ErrInvalid, got %v", c.name, err)
		}
	}
}

func TestSameAnswers(t *testing.T) {
	a := []EvalAnswer{{VideoID: "A", StartMs: 1, EndMs: 2}, {VideoID: "b", StartMs: 3, EndMs: 4}}
	b := []EvalAnswer{{VideoID: "b", StartMs: 3, EndMs: 4}, {VideoID: "a", StartMs: 1, EndMs: 2}}
	if !sameAnswers(a, b) {
		t.Error("same set in another order should match")
	}
	if sameAnswers(a, b[:1]) {
		t.Error("different lengths should not match")
	}
	c := []EvalAnswer{{VideoID: "a", StartMs: 1, EndMs: 2}, {VideoID: "b", StartMs: 3, EndMs: 5}}
	if sameAnswers(a, c) {
		t.Error("a moved boundary is a correction")
	}
}
