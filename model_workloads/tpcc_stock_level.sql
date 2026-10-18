SELECT COUNT(DISTINCT s_i_id)
FROM order_line
JOIN stock ON s_w_id = ol_w_id AND s_i_id = ol_i_id
WHERE ol_w_id = 1
  AND ol_d_id = 1
  AND ol_o_id >= 2981
  AND ol_o_id < 3001
  AND s_quantity < 15
