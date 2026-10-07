-- Label provenance and review.
-- author: 'owner' (written by the person in the Label tab) or 'assistant-draft' (proposed by an
-- AI assistant from transcripts and keyframes). Drafts are excluded from training and evaluation
-- until a person has watched the clip and accepted or corrected it (reviewed_at is set).
ALTER TABLE eval_queries ADD COLUMN reviewed_at TIMESTAMPTZ;
ALTER TABLE eval_queries ADD CONSTRAINT eval_queries_author_check CHECK (author IN ('owner', 'assistant-draft'));
