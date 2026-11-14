(define (domain petu)
  (:requirements :strips :typing)
  (:types lubud maga fosap)
  (:predicates
    (zude ?tapalu - lubud ?tinu - maga)
    (zedurep ?tapalu - lubud ?bigi - maga ?niru - maga)
    (burobez ?tapalu - lubud ?lipozet - fosap)
    (tabi ?nuzup - fosap ?pepun - fosap)
    (kofuk ?lipozet - fosap)
    (vinu ?lipozet - fosap)
    (deno ?tinu - maga)
    (dubugo ?tinu - maga)
    (fudov ?tinu - maga)
    (nuvoda ?tinu - maga)
    (dukul ?tapalu - lubud)
    (keve ?tapalu - lubud)
    (femo ?tapalu - lubud)
    (foma ?tapalu - lubud ?tinu - maga)
    (niraduv ?tapalu - lubud ?tinu - maga)
    (zokosin ?tinu - maga)
    (vuril ?tinu - maga))

  (:action sogef
    :parameters (?tapalu - lubud ?bigi - maga ?niru - maga ?nuzup - fosap ?pepun - fosap)
    :precondition (and (zude ?tapalu ?bigi) (zedurep ?tapalu ?bigi ?niru)
                       (burobez ?tapalu ?nuzup) (tabi ?nuzup ?pepun))
    :effect (and (zude ?tapalu ?niru) (not (zude ?tapalu ?bigi))
                 (burobez ?tapalu ?pepun) (not (burobez ?tapalu ?nuzup))))

  (:action sebo
    :parameters (?tapalu - lubud ?tinu - maga ?lipozet - fosap ?raven - fosap)
    :precondition (and (zude ?tapalu ?tinu) (deno ?tinu) (burobez ?tapalu ?lipozet)
                       (kofuk ?lipozet) (vinu ?raven))
    :effect (and (not (burobez ?tapalu ?lipozet)) (burobez ?tapalu ?raven)))

  (:action zozum
    :parameters (?tapalu - lubud ?tinu - maga ?nuzup - fosap ?pepun - fosap)
    :precondition (and (zude ?tapalu ?tinu) (fudov ?tinu) (dukul ?tapalu)
                       (femo ?tapalu) (burobez ?tapalu ?nuzup) (tabi ?nuzup ?pepun))
    :effect (and (foma ?tapalu ?tinu) (not (femo ?tapalu))
                 (burobez ?tapalu ?pepun) (not (burobez ?tapalu ?nuzup))))

  (:action vumomos
    :parameters (?tapalu - lubud ?tinu - maga)
    :precondition (and (zude ?tapalu ?tinu) (nuvoda ?tinu) (keve ?tapalu))
    :effect (niraduv ?tapalu ?tinu))

  (:action rifuraz
    :parameters (?tapalu - lubud ?vapak - maga ?tinu - maga)
    :precondition (and (foma ?tapalu ?vapak) (zude ?tapalu ?tinu) (dubugo ?tinu))
    :effect (and (zokosin ?vapak) (femo ?tapalu)
                 (not (foma ?tapalu ?vapak))))

  (:action kibel
    :parameters (?tapalu - lubud ?vapo - maga ?tinu - maga)
    :precondition (and (niraduv ?tapalu ?vapo) (zude ?tapalu ?tinu) (dubugo ?tinu))
    :effect (and (vuril ?vapo) (not (niraduv ?tapalu ?vapo)))))
