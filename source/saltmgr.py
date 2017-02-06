# Main script for salt-management-DMSR.
import argparse
import os
import pickle
import RefuelCore
import genericserpentinput
import pickle
import time
import copy
import scipy.optimize
import subprocess
from RefuelCore import ZfromZAID

# possibly nest this all within a main when done
#def main()
# need to look up how to parse arguments from a few variables,
# sys.argv

# First read command line input
parser = argparse.ArgumentParser(description=
    'saltmgr: an interface to Serpent 2 for molten salt reactor depletion.',
    epilog=
    'feel free to email Gavin Ridley at gridley@vols.utk.edu for any help')

# input file for this script
parser.add_argument('inpfile', metavar='f',type=str, nargs=1,
    help='name of the saltmgr input file')

# output directory for SerpentInputFile objects at each depletion step
parser.add_argument('--outdir',dest='outputdirectory',type=str, nargs=1,
    help='name of output directory',default='inputfileslog')

args = parser.parse_args()
saltmgrinput = args.inpfile # name of saltmgr input file
outdir = args.outputdirectory
originaldir = os.getcwd() # get current working dir

# clear old output directory <>? y or n?
if outdir in os.listdir('.'):
    print 'would you like to delete the old output directory, {}?'.format(outdir)
    response = ''
    while response not in ['y','Y','n','N']:
        response=input('y/n')
    if response in ['N','n']:
        print "Bye."
        quit()
    elif response in ['y','Y']:
        print 'forreal though? press enter to continue, ctrl-C to exit.'
        input()
        print 'deleting old output directory'
    else:
        raise Exception('I SAID Y OR N, HOW DID YOU BREAK THIS')

# now read from the input file.
inpfile = open(saltmgrinput)

# init some variables that are necessary for running
optdict = dict.fromkeys(['maintenance','keffbounds','refuel','absorber','core',
                         'maxiter', 'constflows','fuel','runsettings','maxBurnTime',
                         'burnIncrement']) #contains all options

otheropts = [] # all other options

# --- some options will be in the form of a list. init. ---
optdict['maintenance']=[] # maintain concentration of thorium, keep F excess low, etc
optdict['constflows'] =[] # used for stuff like offgasing, possibly removal of precious metals
optdict['runsettings']=dict.fromkeys('PPN','queue','num_nodes') # set pop <blah>

for line in inpfile:
    
    # split line
    sline = line.split()

    # now go through all options
    if sline[0] == 'set':

        # this is for setting options
        if sline[1]=='refuel':

            #sets the refuel material. is it just more highly enriched fuel, or the fuel?
            if sline[2] == 'moreEnrichedFuel':

                # should be enrichment of refuel mat
                optdict['refuel']=('moreEnrichedFuel',sline[3]) 

            elif sline[2] == 'sameAsFuel':
                
                #this implies making a copy of the fuel material and renaming
                optdict['refuel']=('sameAsFuel', -1.0)

        elif sline[1]=='fuel':

            # save what the name of the fuel material is
            optdict['fuel']=sline[2]

        # these next 3 are for PPN, queue, number of nodes, etc
        elif sline[1] == 'PPN':

            optdict['runsettings']['PPN'] = sline[2]

        elif sline[1] == 'queue':

            optdict['runsettings']['queue'] = sline[2]

        elif sline[1] == 'num_nodes':

            optdict['runsettings']['num_nodes'] = sline[2]



    elif '<' in sline and 'keff' in sline:

        # set keff bounds
        optdict['keffbounds'] = None,None
        optdict['keffbounds'][0] = float(sline[0])
        optdict['keffbounds'][1] = float(sline[2])


    elif sline[0] == 'maintain':

        # this will be a salt maintenance entry.
        quantity = sline[1] #quantity to maintain over depletion

        # make sure it is a supported quantity
        assert quantity in ['fluorideExcess', 'concentration']

        # now save this maintenance request

    elif sline[0] == 'core':

        # should the core be generated by the core writer, or
        # should it be read in from a serpent input file?
        # need to be sure to watch for 'include' statements in the file

        if sline[1] == 'serpentInput':
           
            # name of serpent input file to read from
            optdict['core']=('serpentInput', sline[2])

            #check input exists
            if optdict['core'][1] not in os.listdir('.'):
                raise Exception('input file {} not found in current directory'.format(
                    optdict['core'][1]))

            # the serpent input will later be actually read in

        elif sline[1] == 'DMSR':

            # grab input params
            coresize = sline[2]
            pitch    = sline[3]
            saltfrac = sline[4]
            initenrich = sline[5]
            salt_type = sline[6]

            # save it
            # storing data in tuples is cool i guess
            optdict['core']=('DMSR',(coresize,pitch,saltfrac))

            print 'writing {} meter DMSR core with {} cm pitch and 
                    salt fraction of {}'.format(coresize, pitch,saltfrac)

        elif sline[1] == 'oldObject':

            # this option reads a pickled object of either RefuelCore.SerpentInputFile
            # type or of the genericInput type

            if sline[2] not in os.listdir('.'):

                raise Exception('old core obj {} not found in current dir'.format(
                    sline[2])

            # save it
            optdict['core']=='oldObject',sline[2]

        else:
            
            raise Exception('unknown core option {}'.format(sline[1])

    elif all([x in sline for x in ['maintain', 'in', 'via']):

        # this is a salt maintenance command, where some material is added to maintain
        # a desired quantity in the salt. Initially, I'm including fluoride excess
        # and a concentration maintenance command.
        quantity = sline[1] 
        material = sline[3]
        deltamat = sline[5]
        saltComp = None # component of salt if concentration is controlled
        concentration = None # concentration to aim for (atoms / cmb)


        if quantity == 'conc':
            # which nuclide or element component gets controlled?
            saltComp = sline[7]
            concentration = sline[8]

        elif quantity == 'excessFluoride':
            # need to mitigate excess fluorine nuclei, of course
            pass

        else:
            raise Exception('{} is not a known maintenance option ATM'.format(quantity)

        # then add this on to the list of maintenance commands
        optdict['maintenance'].append(quantity, material, deltamat, saltComp,concentration)

    elif sline[0] == 'constflow':

        # this means a flow that doesnt change in response to other quantities
        # in the salt.
        nuclides = sline[1] # should be comma separated values or 'all'
        numbers  = sline[2] # , separated, and either len matches nuclides or one value
        flowtype = sline[3] # serpent flow type. 1 conserves material amounts, 0 is constant
        mat1     = sline[4]
        mat2     = sline[5]

        # split by comma basis
        nuclides = nuclides.split(',')
        numbers  = numbers.split (',')
        flowtype = int(flowtype)

        # input checks
        if not ( len(nuclides) == len(numbers) or len(numbers)==1 ):
            raise Exception('length of flow numbers should be one or match 
                            number of nuclides/elements given')

        if flowtype not in [0,1,2]:
            raise Exception(' flowtype {} is not a valid serpent flow type'.format(flowtype))

        # now this can be safely appended to constflows
        optdict['constflows'].append( (nuclides, numbers, flowtype, mat1, mat2) )

    elif line !='\n':
        raise Exception('unknown keyword: {}'.format(line)
        

# check input
if None in optdict.values():
    raise Exception('not all required options were set')

# Now take that, and deplete!
# load a serpent input file, or generate one from the core writer?
if optdict['core'][0] == 'serpentInputFile':
   
    # first off, create a new generic serpent input file object
    myCore=genericserpentinput.genericInput(num_nodes=optdict['runsettings']['num_nodes']
                                              ,PPN=optdict['runsettings']['PPN']
                                              queue=['runsettings']['queue'])

    # try to read in the serpent input file and save all of its options, materials, etc
    serpentInpFile = open(optdict['core'][1], 'r')

    # the first thing I'd like to do is search for all of the materials and their
    # names.
    for line in serpentInpFile:

        # split the line
        sline = line.split()

        # check if there is a material
        if sline[0] == 'mat':

            # append all materials to the core object
            myCore.materials.append(SerpentMaterial('serpentoutput',
                                    materialname=sline[1],
                                    materialfile=optdict['core'][1]))

            # is it fuel? this is what really matters.
            if sline[1] == optdict['fuel']:

                # save pointer to the fuel material
                fuel = myCore.materials[-1]

            # is it blanket? also matters a good bit.

        elif sline[0] == 'include':

            # then, also look for any "include" statements in the input. 
            # these must be copied for any test refuel cases ran.

            self.includefiles.append(sline[1])


        elif sline[0]=='set' and sline[1]=='pop':

            # change the kcode settings
            myCore.ChangeKcodeSettings(sline[2],sline[3],sline[4])


        else:

            # just add on the rest to the input file
            # the hope here is that the user used "set pop"
            self.otheropts.append( line )

    # and close the original input file
    serpentInpFile.close()

elif optdict['core'][0] == 'DMSR':

    # create a new DMSR from Dr. Chvala's core writer
    myCore = RefuelCore.SerpentInputFile(core_size=coresize,
                                        salt_type=,
                                        case=1,
                                        salt_fraction=saltfrac,
                                        pitch=pitch,
                                        initial_enrichment=initenrich,
                                        num_nodes=optdict['runsettings']['num_nodes'],
                                        PPN=optdict['runsettings']['PPN'],
                                        queue=optdict['runsettings']['queue'] )

elif optdict['core'][0] == 'oldObject':

    # this simply reads in an old SerpentInputFile or genericInput

    myCore = pickle.load(filehandle)

elif optdict['core'][0] == 'serpentInput':

    raise Exception(' havent written code for this yet lol ')

else:
    raise Exception('bad error here')

# --- initialization ---

burnttime = 0
show_new_Umetal_addition_model_difference=True
refuelrates=[] #empty list
refuelrate=initialguessrefuelrate

#--------------
#temporary:
refuelrate= 0.5 # need to create a better guess on this
#---------------

absorberadditionrates=[]
Umetaladditionrates=[]
absorberadditionrate=0.0
Umetaladditionrate=0.0

burnsteps=[]
material_densities=[]
successful_keffs=[]
successful_refuelrates=[]
successful_absorberrates=[]
successful_Umetaladditionrates=[]
absorbertestrhos=[]
refueltestrhos=[]
attempted_absorber_rates=[]
attempted_refuel_rates=[]
absorber_sigmas=[]
refuel_sigmas=[]

#---------------------------#

starttime=time.asctime()
print "Starting the refuelling simulation at {0}".format(starttime)
print "First input file is being refuelled at {0} ccm/s.".format(initialguessrefuelrate)


iternum=0 #keeps track of number of iterations needed to solve for refuel rate in a given depletion step

#create a directory for storing InputFile pickles too. yum
if outdir not in listdir('.'):
    subprocess.call(['mkdir', outdir])

#loop through all materials, and give them the appropriate Z to 
# oxidation number mapping. this can be dynamically changed if needed.
for mat in myCore.materials:
    mat.Z2ox = mat.CalcExcessFluorine(ret_z2charge=True)
    #debug
    print mat.Z2ox
    print 'playin it safe rn'
    quit()

raise Exception(' need to add a definition of depltion sequence. define "daystep"')

# BURN BABY BURN
while burnttime < maxburntime:

    # set all of the constant material flows, firstly
    for nuc,num,flowt in optdict['constflows']:

        if flowt == 0: #flow type = 0
            assert nuc[0] == 'all' #constant volume must mean all flow
            myCore.AddFlow(mat1, mat2, nuc[0], num[0], 0)

        elif flowt == 1:
            myCore.AddFlow(mat1, mat2, nuc, num, 1)

        elif flowt ==2:
            raise Exception('OK, does anyone actually know what type 2 flows do??')

    # now loop through all of the "salt management" quantities.
    # just make a list of the quantities of interest (eg fluorideExcess),
    # and then calculate the flows needed to mitigate them.
    for quantity, controlmaterial, additive, saltcomp,concentration in optdict['maintenance']:

        controlpoint = myCore.getMat(controlmaterial) # pointer to control material
        additivepoint = myCore.getMat(additive) # pointer to additive

        # generally, calculate what the current quantity of interest is.
        # first, cover the case of fluoride.
        if quantity is 'excessFluoride':

            # returns total amount of excess fluoride in moles. NOT a concentration.
            important_quantity = controlpoint.CalcExcessFluorine()
            
            # to figure out how much additive to, well, add, check how 
            # much positive charge it would give to the salt, given the assumed
            # oxidation states.
            # the best way to do this, IMO, is calculate how many moles of charge per ccm
            # the additive has, and then set a constant volume flow over the next step that fully
            # mitigates all excess fluoride.
            molPositiveV = 0.0 # init, moles positive volumetric
            for iso in additivepoint.isotopic_content.keys():

                z = ZfromZAID(iso)
                # add how much charge per ccm it would contribute if it were in the 
                # control material
                molPositiveV += additivepoint.isotopic_content[iso]*controlpoint.Z2ox[z]/0.602214086

            # now the flow rate is simple!!!
            totalToAdd = important_quantity / molPositiveV # total volume of additive to add

            # now, if totalToAdd is negative, you need to add an oxidizing agent, or do nothing
            # assuming your fuel becomes more oxidizing with time (TerraPower claims theirs doesnt
            # do this, personal comm. Jeff Latkowski )
            if totalToAdd < 0.0:
                raise Exception(" fuel became more reducing. WHY WHY WHY ")

            myCore.SetConstantVolumeFlow(additive, controlmaterial, totalToAdd/float(daystep*24*3600) )

        elif quantity is 'conc':

            # this is just a concentration of some material, eg thorium.
            # it may be either a unique isotope, or an element Z value. 
            # if it is a Z value, sum across isotopes.

            # NOTE probably need to add try/except KeyError expressions to handle when a nuclide is missing

            if len(saltcomp) is 1 or len(saltcomp) is 2:
                # implies a Z value
                important_quantity = 0.0 #init
                for iso in controlpoint.isotopic_content.keys():

                    z = ZfromZAID(iso)
                    important_quantity += controlpoint.isotopic_content[iso] if z is saltcomp else 0.0

                # now sum to find the total concentration of Z in the additive, too
                additive_conc = 0.0 # init
                for iso in additivepoint.isotopic_content.keys():

                    z = ZfromZAID(iso)
                    additive_conc += additivepoint.isotopic_content[iso] if z is saltcomp else 0.0


            elif len(saltcomp) is 4 or len(saltcomp) is 5:
                
                # this means that a single isotope is being controlled.
                important_quantity = controlpoint.isotopic_content[saltcomp]

                # also, get its concentration in the additive material.
                additive_conc = additivepoint.isotopic_content[saltcomp]

            else:

                raise Exception('unknown salt component {} in {}'.format(saltcomp, controlmat))

            # Now that both concentrations are known, the flow needed to maintain the desired concentration
            # is now known.
            # the total excess quantity in the fuel salt is:
            total_excess = (important_quantity - concentration) * controlpoint.volume #units of 1/(cmb) * cm^3

            if total_excess < 0.0:

                # need to add material
                flow = -1.0 * total_excess / (additive_conc * additivepoint.volume) / float(daystep*24*3600) #no unit
                
                # set the flow (ccm/s)
                myCore.SetConstantVolumeFlow(additive,controlmaterial,

            elif total_excess > 0.0:

                # need to remove material
                flow = total_excess / (important_quantity * controlpoint.volume)  / float(daystep*24*3600)

        else:
            raise Exception('unknown quantity {}'.format(quantity))

