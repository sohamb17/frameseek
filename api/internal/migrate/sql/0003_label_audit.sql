-- Assistant-written labels with a random human spot-check.
-- author 'assistant': written and checked by an AI assistant (transcripts, OCR, 2 s frames), no
-- human pass. A random subset is flagged spot_check; a person watches those in the Label tab and
-- records a verdict. Spot-check verdicts are reported as the label set's measured agreement.
ALTER TABLE eval_queries DROP CONSTRAINT eval_queries_author_check;
ALTER TABLE eval_queries ADD CONSTRAINT eval_queries_author_check CHECK (author IN ('owner', 'assistant-draft', 'assistant'));
ALTER TABLE eval_queries ADD COLUMN spot_check BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE eval_queries ADD COLUMN review_verdict TEXT CHECK (review_verdict IN ('accepted', 'corrected', 'rejected'));
