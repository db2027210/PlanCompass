def cap_replay_fields:
  walk(
    if type == "object" then
      (if has("formal_search_initial_replay_top_k") then
         .formal_search_initial_replay_top_k = 5
       else . end)
      | (if has("formal_search_second_replay_top_k") then
           .formal_search_second_replay_top_k = 5
         else . end)
      | (if has("formal_search_max_replay_top_k") then
           .formal_search_max_replay_top_k = 5
         else . end)
      | (if has("formal_search_replay_cumulative_tiers") then
           .formal_search_replay_cumulative_tiers = [5]
         else . end)
      | (if has("candidate_replay_top_k") then
           .candidate_replay_top_k = ([.candidate_replay_top_k, 5] | min)
         else . end)
      | (if has("real_validation_top_k") then
           .real_validation_top_k =
             (if .real_validation_top_k == 0 then 5
              else ([.real_validation_top_k, 5] | min) end)
         else . end)
      | (if has("adaptive_replay_controller")
            and (.adaptive_replay_controller | type) == "object" then
           .adaptive_replay_controller.max_budget = 5
           | .adaptive_replay_controller.batch_size = 5
           | .adaptive_replay_controller.min_evidence = 5
           | .adaptive_replay_controller.max_expansions = 0
         else . end)
    else . end
  );

walk(
  if type == "string" and contains($old_attempt) then
    split($old_attempt) | join($new_attempt)
  else . end
)
| cap_replay_fields
