(define (domain tinu)
  (:requirements :strips :typing)
  (:types lubud maga fosap)
  (:predicates
    (zude ?raven - lubud ?niru - maga)
    (zedurep ?raven - lubud ?duteme - maga ?vapo - maga)
    (burobez ?raven - lubud ?bigi - fosap)
    (tabi ?lipozet - fosap ?nuzup - fosap)
    (kofuk ?bigi - fosap)
    (vinu ?bigi - fosap)
    (deno ?niru - maga)
    (dubugo ?niru - maga)
    (fudov ?niru - maga)
    (nuvoda ?niru - maga)
    (dukul ?raven - lubud)
    (keve ?raven - lubud)
    (femo ?raven - lubud)
    (foma ?raven - lubud ?niru - maga)
    (niraduv ?raven - lubud ?niru - maga)
    (zokosin ?niru - maga)
    (vuril ?niru - maga))

  (:action sogef
    :parameters (?raven - lubud ?duteme - maga ?vapo - maga ?lipozet - fosap ?nuzup - fosap)
    :precondition (and (zude ?raven ?duteme) (zedurep ?raven ?duteme ?vapo)
                       (burobez ?raven ?lipozet) (tabi ?lipozet ?nuzup))
    :effect (and (zude ?raven ?vapo) (not (zude ?raven ?duteme))
                 (burobez ?raven ?nuzup) (not (burobez ?raven ?lipozet))))

  (:action sebo
    :parameters (?raven - lubud ?niru - maga ?bigi - fosap ?pepun - fosap)
    :precondition (and (zude ?raven ?niru) (deno ?niru) (burobez ?raven ?bigi)
                       (kofuk ?bigi) (vinu ?pepun))
    :effect (and (not (burobez ?raven ?bigi)) (burobez ?raven ?pepun)))

  (:action zozum
    :parameters (?raven - lubud ?niru - maga ?lipozet - fosap ?nuzup - fosap)
    :precondition (and (zude ?raven ?niru) (fudov ?niru) (dukul ?raven)
                       (femo ?raven) (burobez ?raven ?lipozet) (tabi ?lipozet ?nuzup))
    :effect (and (foma ?raven ?niru) (not (femo ?raven))
                 (burobez ?raven ?nuzup) (not (burobez ?raven ?lipozet))))

  (:action vumomos
    :parameters (?raven - lubud ?niru - maga)
    :precondition (and (zude ?raven ?niru) (nuvoda ?niru) (keve ?raven))
    :effect (niraduv ?raven ?niru))

  (:action rifuraz
    :parameters (?raven - lubud ?tapalu - maga ?niru - maga)
    :precondition (and (foma ?raven ?tapalu) (zude ?raven ?niru) (dubugo ?niru))
    :effect (and (zokosin ?tapalu) (femo ?raven)
                 (not (foma ?raven ?tapalu))))

  (:action kibel
    :parameters (?raven - lubud ?vapak - maga ?niru - maga)
    :precondition (and (niraduv ?raven ?vapak) (zude ?raven ?niru) (dubugo ?niru))
    :effect (and (vuril ?vapak) (not (niraduv ?raven ?vapak)))))
