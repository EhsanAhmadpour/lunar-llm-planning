;; Lunar rover domain (plain STRIPS + :typing).
;; Rovers drive between waypoints, spend battery (one level per move or
;; sample), recharge only in sunlight, and can only transmit data to Earth
;; from waypoints that are in communication view.
(define (domain lunar)
  (:requirements :strips :typing)
  (:types rover waypoint level)
  (:predicates
    (at ?r - rover ?w - waypoint)
    (can-traverse ?r - rover ?from - waypoint ?to - waypoint)
    (battery ?r - rover ?l - level)
    (next-lower ?l1 - level ?l2 - level)
    (below-full ?l - level)
    (full-level ?l - level)
    (sunlit ?w - waypoint)
    (comm-visible ?w - waypoint)
    (sample-site ?w - waypoint)
    (image-target ?w - waypoint)
    (has-sampler ?r - rover)
    (has-camera ?r - rover)
    (store-empty ?r - rover)
    (holding-sample ?r - rover ?w - waypoint)
    (holding-image ?r - rover ?w - waypoint)
    (sample-sent ?w - waypoint)
    (image-sent ?w - waypoint))

  ;; Driving costs one battery level; impossible at the lowest level.
  (:action move
    :parameters (?r - rover ?from - waypoint ?to - waypoint ?l1 - level ?l2 - level)
    :precondition (and (at ?r ?from) (can-traverse ?r ?from ?to)
                       (battery ?r ?l1) (next-lower ?l1 ?l2))
    :effect (and (at ?r ?to) (not (at ?r ?from))
                 (battery ?r ?l2) (not (battery ?r ?l1))))

  ;; Only in sunlight; jumps straight to the full level. Not applicable when already full.
  (:action recharge
    :parameters (?r - rover ?w - waypoint ?l - level ?lfull - level)
    :precondition (and (at ?r ?w) (sunlit ?w) (battery ?r ?l)
                       (below-full ?l) (full-level ?lfull))
    :effect (and (not (battery ?r ?l)) (battery ?r ?lfull)))

  (:action collect-sample
    :parameters (?r - rover ?w - waypoint ?l1 - level ?l2 - level)
    :precondition (and (at ?r ?w) (sample-site ?w) (has-sampler ?r)
                       (store-empty ?r) (battery ?r ?l1) (next-lower ?l1 ?l2))
    :effect (and (holding-sample ?r ?w) (not (store-empty ?r))
                 (battery ?r ?l2) (not (battery ?r ?l1))))

  (:action take-image
    :parameters (?r - rover ?w - waypoint)
    :precondition (and (at ?r ?w) (image-target ?w) (has-camera ?r))
    :effect (holding-image ?r ?w))

  (:action send-sample
    :parameters (?r - rover ?site - waypoint ?w - waypoint)
    :precondition (and (holding-sample ?r ?site) (at ?r ?w) (comm-visible ?w))
    :effect (and (sample-sent ?site) (store-empty ?r)
                 (not (holding-sample ?r ?site))))

  (:action send-image
    :parameters (?r - rover ?target - waypoint ?w - waypoint)
    :precondition (and (holding-image ?r ?target) (at ?r ?w) (comm-visible ?w))
    :effect (and (image-sent ?target) (not (holding-image ?r ?target)))))
