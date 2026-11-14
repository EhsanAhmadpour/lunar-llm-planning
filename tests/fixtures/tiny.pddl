; Hand-made tiny problem used by the validator tests (uses domain/lunar.pddl).
; w1 base (sunlit) -- w2 (comm-visible) -- w3 (sample site, image target, sunlit); w4 unreachable terrain.
(define (problem tiny)
  (:domain lunar)
  (:objects rover1 - rover w1 w2 w3 w4 - waypoint b0 b1 b2 b3 - level)
  (:init
    (at rover1 w1) (battery rover1 b3) (store-empty rover1) (has-sampler rover1) (has-camera rover1)
    (can-traverse rover1 w1 w2) (can-traverse rover1 w2 w1)
    (can-traverse rover1 w2 w3) (can-traverse rover1 w3 w2)
    (next-lower b3 b2) (next-lower b2 b1) (next-lower b1 b0)
    (below-full b0) (below-full b1) (below-full b2) (full-level b3)
    (sunlit w1) (sunlit w3) (comm-visible w2)
    (sample-site w3) (image-target w3))
  (:goal (and (sample-sent w3) (image-sent w3))))
