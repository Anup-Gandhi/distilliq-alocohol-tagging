// One-line explanations shown under each column header on the Results page.
// Looked up as "<table>.<column>" first, then "<column>"; a column with no entry shows nothing.

const PASS = { llm_tags: 'pass 1', llm_retags: 'pass 2', llm_final_tags: 'pass 3' } as const

const common: Record<string, string> = {
  source_row_id: 'Row id from the Athena source table',
  base_value: 'Item name as written on the menu / POS',
  options: 'Candidate product names offered to the model',
  source_file: 'CSV file the row was uploaded from',
  data: 'All columns of the uploaded CSV row',
  insert_dttm: 'When the row was inserted in Athena',
  loaded_at: 'When the row was copied from Athena',
  source_insert_dttm: 'Athena insert time of the source row',
  tagged_at: 'When the model answered',
  model: 'Model that answered',
  api_name: 'API key name used for the call',
  tag: 'Product name the model chose (empty if skipped or failed)',
  selected_option: 'The option it picked, if it picked one from the list',
  suggested_name: 'A new name, when no option fit',
  is_alcoholic: 'Whether the model says the item is alcohol',
  confidence: 'Model\'s own confidence: high / medium / low',
  reason: 'Model\'s explanation for its answer',
  judgements: 'Per-option verdicts from the model',
  sources: 'Web sources the model cited',
  error: 'API or parsing error; the row has no answer',
  skip_reason: 'Why the model gave no tag',
  misc_category: 'What kind of non-liquor item it is',
  first_model: 'Pass 1 model',
  decision: 'How the answer was reached',
  dispute_type: 'What pass 1 and pass 2 disagreed on',
  disputed_point: 'The disagreement, in the model\'s words',

  tag_1: 'Pass 1 tag', tag_2: 'Pass 2 tag (review of Misc / blank pass 1 rows)', tag_3: 'Pass 3 tag',
  model_1: 'Pass 1 model', model_2: 'Pass 2 model', model_3: 'Pass 3 model',
  tag_1_model: 'Pass 1 model', tag_2_model: 'Pass 2 model', tag_3_model: 'Pass 3 model',
  is_alcoholic_1: 'Pass 1 says alcohol', is_alcoholic_2: 'Pass 2 says alcohol', is_alcoholic_3: 'Pass 3 says alcohol',
  is_alcoholic_any: 'Pass 1 or pass 2 says alcohol',
  skip_reason_1: 'Why pass 1 skipped', skip_reason_2: 'Why pass 2 skipped', skip_reason_3: 'Why pass 3 skipped',
  reason_1: 'Pass 1 explanation', reason_2: 'Pass 2 explanation',
  error_1: 'Pass 1 error', error_2: 'Pass 2 error',
  confidence_1: 'Pass 1 confidence', confidence_2: 'Pass 2 confidence',
  suggested_name_1: 'Pass 1 new name', suggested_name_2: 'Pass 2 new name',
  selected_option_1: 'Option pass 1 picked', judgements_1: 'Pass 1 per-option verdicts',
  sources_1: 'Pass 1 web sources', sources_2: 'Pass 2 web sources',
  tagged_at_1: 'When pass 1 answered', tagged_at_2: 'When pass 2 answered',
  tags_match: 'Pass 1 and 2 tags are the same (ignoring case)',
  is_match: 'Pass 1 and 2 agree; empty until both answered',
  needs_third_tag: 'Passes 1 and 2 disagree, so pass 3 must decide',

  final_tag: 'The answer: the product name, or Misc - Not a Liquor',
  final_tag_model: 'Model whose answer became final_tag (pass 2 for agreed Misc, else pass 3)',
  status: 'Where the row is in the pipeline',
  needs_pass_2: 'Pass 1 said Misc or left it blank, so pass 2 reviews it',
  needs_pass_3: 'Pass 3 must name this row',

  first_tag: 'Pass 1 tag', second_tag: 'Pass 2 tag', third_tag: 'Pass 3 tag',
  final_tag_reason: 'Why this is the final tag, or why there is none',
  first_tag_sources: 'Pass 1 web sources', second_tag_sources: 'Pass 2 web sources',
  first_tagged_at: 'When pass 1 answered',

  human_tag: 'Tag a person gave in the uploaded CSV',
  llm_tag: 'Model\'s tag (final if there is one, else pass 1)',
  llm_model: 'Model behind llm_tag',
  comparison: 'Human tag vs model tag',
  claude_tag: 'Tag from the old claude_tags table', claude_model: 'Model behind claude_tag',
  claude_skip_reason: 'Why the old run skipped', llm_skip_reason: 'Why pass 1 skipped',
  claude_error: 'Old run error', llm_error: 'Pass 1 error',
  claude_vs_llm: 'Old run vs pass 1', human_vs_claude: 'Human vs old run', human_vs_llm: 'Human vs pass 1',
  claude_vs_llm_vs_human: 'All three compared',
}

const specific: Record<string, string> = {
  'final_results.reason': 'Explanation behind the final tag',
  'final_results.is_alcoholic': 'Final answer: is it alcohol',
  'llm_tag_votes.decision': 'How the final tag was voted',
  'llm_tag_votes.skip_reason': 'Why the row has no final tag',
  'human_tag_comparison.decision': 'How the model\'s final tag was voted',
  'llm_retags.first_tag': 'Pass 1 tag this review was given',
  'csv_records.tagged_at': 'When the CSV was uploaded',
  'topk_options.model': 'Source flag from Athena',
  'llm_final_tags.tag_1': 'Pass 1 tag (not shown to pass 3)', 'llm_final_tags.tag_2': 'Pass 2 tag (not shown to pass 3)',
}

export function columnHelp(table: string, column: string): string {
  const key = `${table}.${column}`
  if (specific[key]) return specific[key]
  const pass = PASS[table as keyof typeof PASS]
  // the raw pass tables share column names; say which pass they belong to
  if (pass && ['tag', 'model', 'reason', 'error', 'skip_reason', 'tagged_at', 'is_alcoholic'].includes(column))
    return `${common[column]} (${pass})`
  return common[column] ?? ''
}
