-- Какой моделью эмбеддингов проиндексирован документ: при смене модели индексатор
-- пересчитывает векторы, а не смешивает несопоставимые пространства в одной таблице.
ALTER TABLE kb.documents ADD COLUMN IF NOT EXISTS embedding_model text;
COMMENT ON COLUMN kb.documents.embedding_model IS 'Модель эмбеддингов, которой посчитаны векторы чанков документа';
