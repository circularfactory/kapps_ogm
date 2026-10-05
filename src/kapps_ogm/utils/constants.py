from kapps_triplestore_interface import IRI


PROPERTY_TYPES = {
    IRI("owl:ObjectProperty"): "object",
    IRI("owl:DatatypeProperty"): "data",
    IRI("owl:AnnotationProperty"): "annotation",
    IRI("rdf:Property"): "generic",
}

PROPERTY_CHARACTERISTICS = {
    IRI("owl:FunctionalProperty"): "functional",
    IRI("owl:InverseFunctionalProperty"): "inverse_functional",
    IRI("owl:TransitiveProperty"): "transitive",
    IRI("owl:SymmetricProperty"): "symmetric",
    IRI("owl:AsymmetricProperty"): "asymmetric",
    IRI("owl:ReflexiveProperty"): "reflexive",
    IRI("owl:IrreflexiveProperty"): "irreflexive",
}
