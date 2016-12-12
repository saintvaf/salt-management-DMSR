import sys
sys.path.append('../../../../../../source')

from RefuelCore import SerpentMaterial,SerpentInputFile, RefuelorAbsorberFit, inversequadraticinterp, secant, getadditionrates
import time #for pauses
import numpy as np
import pickle #because pickles are delicous ... actually for dumping binary objects to a file. allows recording of previously run input files.
import copy
import os
from os import listdir
import scipy.optimize
#Print out info for debug:
debug=True

#Remove functionality for debug:
reallydebug=False

#-------------------------------------------#
#              Input file setup             #
#-------------------------------------------#


os.chdir('inputfileslog')
ls=os.listdir('.')
days=[]
for file in ls:
    nums=[char for char in file if char.isdigit()] #pull out list of numbers
    numstring="".join(nums) #put em together
    day=int(numstring)
    days.append(day)
days.sort() #put em in order
#now we want to grab fluorine excess calculations for each step
excess_F_moles_lower=[]
excess_F_moles_upper=[]
excess_F_moles_doligez=[]
convratios=[]
betaEffs=[]

#where to pick up?
burnttime=max(days)

for dayval in days:
    fh=open("inputday{0}.dat".format(dayval), 'r')
    p=pickle.load(fh)
    excess_F_moles_upper.append( p.CalcExcessFluorine(printfexcess=False) )
    convratios.append(p.convratio)
    betaEffs.append(p.betaEff)
    #need some code to check that bumat output is indeed printing atom densities beside each nuclide
    atomdensitysum=0.0
    for mat in p.materials:
        if mat.materialname=='fuel':
            for iso in mat.isotopic_content.keys():
                atomdensitysum += float(mat.isotopic_content[iso])
            break

    fh.close()

#backing up.
os.chdir('..')

#and to pick back up where we left off:
inputfile = p
inputfile.queue='gen5'
inputfile.PPN=8
inputfile.num_nodes=15

burnsteps, successful_keffs, successful_refuelrates, successful_absorberrates, successful_Umetaladditionrates=getadditionrates('log')

#set the burn increment
timeincrement=7.

#the refuelling will be done through two tanks:
#    1. a material identical to the fuel but at higher enrichment
#    2. solid uranium metal, which can be substituted for some of the refuel
#       salt flow to create a UF_eff mix when necessary. Not focusing on this at
#       the moment. <- not yet implemented

refuel_tank_volume=1e6 #ccm
refuel_enrich = .2 
umetal_enrich = .00 #using approximately depleted uranium as a reducing agent

#add some materials to the input file that are needed for the refueling process.
# refuel material, room for xenon and noble gases to bleed off, room for fuel displaced by the addition of fresh fuel, and some burnable absorber
#now, lets create an initial guess for the refuel rate.
#-----------------------
initialguessrefuelrate=successful_refuelrates[-1]

#----------------------------------------------#
#          Burnup with refuelling              #
#----------------------------------------------#

#total time to burn, and a counter variable
maxburntime=365*10 #days

#when collecting rho vs refuel data, how many cases to run?
num_test_cases=5

#Keff bounds:
lowerkeffbound=.998
upperkeffbound=1.002

#---------------------------#
#       Initialization      #
#---------------------------#
show_new_Umetal_addition_model_difference=True
refuelrates=[] #empty list
refuelrate=initialguessrefuelrate
#--------------
absorberadditionrate=0.0
Umetaladditionrate=0.0

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

#Make a directory for copying InputFile objects into for later analysis
import subprocess

#create a directory for storing InputFile pickles too. yum

def findNecessaryUMetalFlowRate(Q0, corevolume, extraUmetal, timestep):
        """The expression that determines the volumetric flow rate of uranium needed to mitigate fluorine is
         trancendental, so this function solves that problem.

         Inputs:
                Q0 -- this is the volumetric flow already coming out of the core, due to adding more fresh fuel, burnable absorber, etc...
                      units: ccm/s
                corevolume -- volume of the whole core in ccm
                      units: ccm/s
                extraUmetal -- the number of moles of additional uranium that needs to be in the core by the end of the depletion step
                      units: moles
                timestep -- length of time of the depletion step
                      units: days"""
        molarmass = 91.224 #molar mass of depleted uranium in g/mol
        rho = 6.52 # grams / ccm for uranium metal
        #Solve using inverse quadratic interpolation.
        #Let's get an initial guess by using the flow needed with zero outflow:
        timestep_seconds = timestep * 24. * 3600. #converting days to seconds

        #let's define the expression that a zero is found to.
        #derivations are on paper. :)
        #finding a root to this wrt Qmetal gives the correct flow of metal needed
        expr = lambda Qmetal: np.float_((Q0+Qmetal)*molarmass*extraUmetal/corevolume**2/Qmetal/rho+np.exp(-(Q0+Qmetal)/corevolume**2*timestep_seconds)-1.0)
        try:
                scipyanswer=scipy.optimize.brenth(expr,1e-13,1.0)
        except ValueError:
                scipyanswer=0.0
        #this is what the old guess was, for historical reasons:
        guess0=np.float_(extraUmetal* molarmass / rho / timestep_seconds)
        return scipyanswer

while burnttime<maxburntime:
        #----------------------------------------------------------#
        #     Calc. the U metal addition rate if needed            #
        #----------------------------------------------------------#
        #only add U metal if there is both refuelling ( not good for excess reactivity )
        #initially have Umetaladdition rate at 0. then change if needed.
        #assuming here that fluorine leaving due to displacement of fuel is negligible
        Umetal = None #define as None before it has been found
        #first off, be sure there is still a Umetal material still in the input. If not, more must be added.
        for mat in inputfile.materials: #finding the Umetal material data
                if mat.materialname=='Umetal':
                        Umetal=mat
                        break
        if Umetal==None:
                #this indicates a uranium metal should be added to the input file
                # this happens when serpent does not include material output for Umetal, so it must be re-added
                inputfile.AddUraniumMetal(umetal_enrich, refuel_tank_volume)
                print "Re-adding uranium metal to input file because it was not found in depletion output."
                #now loop through to double check it was found
                for mat in inputfile.materials: #finding the Umetal material data
                        if mat.materialname=='Umetal':
                                Umetal=mat
                                break
        #units of Umetal.atomdensity are atoms / cm-b
        #if uranium still wasn't added for some reason, throw an error
        if Umetal==None:
                print "Umetal was not found in the input file."
                print inputfile.materials
                raise Exception("need Umetal for fluorine treatment. unable to add for some reason.")

        if burnttime == 0:
                print "no U metal addition because there isn't depletion yet"
                fluorineexcess=None
        if burnttime > 0:
                fluorineexcess=inputfile.CalcExcessFluorine()
        if burnttime > 0 and fluorineexcess > 0.0: # and refuelrate > 0.0:
                #it is easier to calculate this if we know how many moles of uranium per ccm are in the Umetal material
                Umetal_molar_density=float(Umetal.atomdensity)/0.602214086 # moles / ccm
                #Calculating the right amount of uranium metal to add is actually kinda hard. A transcendatal equation results from
                # the expression you get for uranium in the core as a function of time. thanks scipy.
                Umetaladditionrate=findNecessaryUMetalFlowRate(refuelrate+absorberadditionrate,inputfile.fuelvolume,fluorineexcess/4.,timeincrement)

        elif fluorineexcess < 0.0:
                print "there is a shortage of fluorine in the core. letting burn step happen without U metal addition."
        #elif refuelrate == 0.0:
        #        print "Excess reactivity present. not adding any U metal as a result."
        else:
                raise Exception("check line 149 for error")
                
        #----------------------------------------------------------#
        # High resolution run, test if guessed refuel rate is correct  #
        #----------------------------------------------------------#

        #set the refuel rates
        inputfile.SetConstantVolumeFlow('refuel','fuel',refuelrate) #volumetric addition of new fuel
        inputfile.SetConstantVolumeFlow('absorbertank','fuel', absorberadditionrate) #addition of GdF3
        inputfile.SetConstantVolumeFlow('Umetal', 'fuel', Umetaladditionrate)
        inputfile.SetConstantVolumeFlow('fuel','excessfueltank',refuelrate+absorberadditionrate+Umetaladditionrate) #displace some fuel from the core when adding stuff
        inputfile.SetRatioFlow('fuel','offgastank',['Xe','Kr','Ar','Ne','He','Ra'],.02) #noble gas bleedoff. see paper for derivation of .02.

        #submit job
        inputfile.SubmitJob()

        #wait
        while not(inputfile.IsDone()):
                time.sleep(10)
                #print inputfile.IsDone(getstatus=True)

        #collect results
        keff,relerror=inputfile.ReadKeff(returnrelerror=True) #get absorbtion estimate keff. ABS keff tends to have the least relative error from what ive seen
        #go ahead and add results to test case info
        if refuelrate> 0:
                refueltestrhos.append( (keff-1.)/keff )
                attempted_refuel_rates.append( refuelrate )
                refuel_sigmas.append(relerror)
        elif absorberadditionrate > 0.:
                absorbertestrhos.append( (keff-1.)/keff )
                attempted_absorber_rates.append(absorberadditionrate)
                absorber_sigmas.append(relerror)
        elif refuelrate == 0.0 and absorberadditionrate==0.0:
                absorbertestrhos.append( (keff-1.)/keff )
                attempted_absorber_rates.append(absorberadditionrate)
                absorber_sigmas.append(relerror)
                refueltestrhos.append( (keff-1.)/keff )
                attempted_refuel_rates.append( refuelrate )
                refuel_sigmas.append(relerror)
        else:
                print absorberadditionrate,refuelrate
                raise Exception("absorber addition rate or refuel rate took on an unreasonable value")


        if lowerkeffbound > keff or upperkeffbound < keff:

                #------------------------------------------------------------------#
                # Many test cases with low resolution to solve for new refuel rate #
                #------------------------------------------------------------------#

                #make some input files with different refuel rates, identical to the last
                testinputfiles=[copy.copy(inputfile) for x in range(num_test_cases)] #make <num_test_cases> copies of the original

                #now set new refuel rates to collect data
                refuelrates_to_try=np.random.random_sample(num_test_cases) * refuelrate*2.5
                absorberaddition_rates_to_try=np.random.random_sample(num_test_cases) * absorberadditionrate * 2.0
                for i,file in enumerate(testinputfiles):
                        if refuelrates_to_try[i] > 10.:
                                print "there is way too much fresh fuel being added. this is because of a curve fit with poor data."
                                print "Reducing flow to reasonable guess value for data collection"
                                refuelrates_to_try[i] = np.random.random_sample(1)[0] * 30.
                        #set the refuel rates
                        file.SetConstantVolumeFlow('refuel','fuel',refuelrates_to_try[i]) #volumetric addition of new fuel
                        file.SetConstantVolumeFlow('absorbertank','fuel', absorberaddition_rates_to_try[i]) #addition of GdF3
                        file.SetConstantVolumeFlow('fuel','excessfueltank',refuelrates_to_try[i]+absorberaddition_rates_to_try[i]) #displace some fuel from the core when adding stuff
                        file.SetRatioFlow('fuel','offgastank',['Xe','Kr','Ar','Ne','He','Ra'],.02)
                        file.ChangeKcodeSettings(10000,500,100) #these get run at lower resolution, but with more cases
                        file.num_nodes=3
                        file.SetInputFileName('nafkftest{0}'.format(i))
                        file.SubmitJob()

                #now wait for the jobs to finish
                while not all([file.IsDone() for file in testinputfiles]):
                        #print '--test job status--'
                        for file in testinputfiles:
                                pass
                                #print file.IsDone(getstatus=True)
                        #print '----\n\n'
                        time.sleep(10)

                #fit data
                #if absorber data was measured, only record that. and vice-versa.
                if absorberadditionrate!=0.0:
                        for file in testinputfiles:
                               keff, sigma = file.ReadKeff(returnrelerror=True)
                               rho = (keff - 1) /keff
                               absorbertestrhos.append(rho)
                               absorber_sigmas.append(sigma)
                        attempted_absorber_rates.extend(absorberaddition_rates_to_try)

                        # attempted_refuel_rates.extend(refuelrates_to_try)
                        # myfit=RefuelorAbsorberFit(inputfile)
                        # myfit.fitcurve(attempted_refuel_rates, refueltestrhos, refuel_sigmas, printparams=debug)
                        # refuelrate=myfit.guessfunctionzero()

                        myfit=RefuelorAbsorberFit(inputfile, fittype="Absorber")
                        myfit.fitcurve(attempted_absorber_rates, absorbertestrhos, printparams=debug)
                        absorberadditionrate=myfit.guessfunctionzero()
                        #sometimes, an addition rate of zero is needed, so this is checked.
                        if iternum==3:
                                absorberadditionrate=0.0
                        #absorberparams, absorberp_cov = curve_fit(absorber_fit , attempted_absorber_rates, absorbertestrhos, sigma=absorber_sigmas, p0=(1,-1e6,-1))
                        #aa,ab,ac=tuple(absorberparams) #these are the ones for the absorber curve
                        #absorberadditionrate=FindRefuelCurveZero(aa,ab,ac, 0)
                        if np.isnan(absorberadditionrate):
                                absorberadditionrate = .5 * initialguessrefuelrate #just give it some valid value to collect more data
                        refuelrate=0.0
                        print "------------Iteration {0} at {1} days---------------".format(iternum, burnttime)
                        print "currently attempting to add burnable absorber.\n Absorber addition rate is {0} ccm/s.".format(absorberadditionrate)
                        print "attempted absorber addition rates are:"
                        print attempted_absorber_rates
                        print "resulting reactivities are:"
                        print absorbertestrhos
                elif refuelrate!=0.0 or (refuelrate==0.0 and absorberadditionrate==0.0):
                        for file in testinputfiles:
                                keff, sigma = file.ReadKeff(returnrelerror=True)
                                rho = (keff - 1) /keff
                                refueltestrhos.append(rho)
                                refuel_sigmas.append(sigma)
                        attempted_refuel_rates.extend(refuelrates_to_try)
                        myfit=RefuelorAbsorberFit(inputfile)
                        myfit.fitcurve(attempted_refuel_rates, refueltestrhos, printparams=debug)
                        refuelrate=myfit.guessfunctionzero()
                        # if the zero to the curve was unrealistic, more data should be collected
                        if iternum==3:
                                refuelrate=0.0
                        if np.isnan(refuelrate):
                                refuelrate= 1.5* initialguessrefuelrate
                        elif refuelrate > 50:
                                refuelrate=np.random.random_sample(1)[0] * 30
                        absorberadditionrate=0.0
                        print "----------Iteration {0} at {1} days------------".format(iternum,burnttime)
                        print "refuel rate attempted in this iteration is {0} ccm/s".format(refuelrate)
                        print "attempted refuel rates are:"
                        print attempted_refuel_rates
                        print "resulting reactivities are:"
                        print refueltestrhos
                else:
                        raise Exception("for some reason, there was both absorber and new fuel being added. This is unreasonable.")

                if refuelrate < 0.0:
                        #this means that absorber needs to be added, so make a switch to that with a good initial guess
                        refuelrate=0.0
                        absorberadditionrate=initialguessrefuelrate #guess a reasonable amount of absorber to add
                        print "Switching to addition of burnable absorber."

                if absorberadditionrate < 0.0:
                        absorberadditionrate=0.0
                        refuelrate=initialguessrefuelrate #just a guess
                        print "Switching from absorber addition to refuelling."
                iternum+=1
        #else if the keff value was acceptable
        elif lowerkeffbound <= keff or upperkeffbound >= keff:

                #-------------------------#
                # Refuel rate was correct #
                #-------------------------#

                #clear the data that was used for calculating the new flow rate
                absorbertestrhos=[]
                refueltestrhos=[]
                attempted_absorber_rates=[]
                attempted_refuel_rates=[]
                absorber_sigmas=[]
                refuel_sigmas=[]
                iternum=0 #reset refuel rate solver iteration number
                #save the input file's info
                burnsteps.append(burnttime)
                successful_keffs.append(inputfile.ReadKeff())
                inputfile.keff=inputfile.ReadKeff()
                inputfile.convratio = inputfile.ReadConvRatio()
                inputfile.betaEff = inputfile.ReadBetaEff()
                inputfile.GetMaxDamageFlux()
                successful_refuelrates.append(refuelrate)
                successful_absorberrates.append(absorberadditionrate)
                successful_Umetaladditionrates.append(Umetaladditionrate)
                print("----------Keff and burn time at day {0} (ccm/s) -----------".format(burnttime))
                print burnsteps
                print successful_keffs
                print("----------Refuel rates so far (ccm/s) -----------")
                print successful_refuelrates
                print("----------absorber rates so far (ccm/s) --------")
                print successful_absorberrates
                print("------------Umetal addition rates so far (ccm/s) -----------")
                print successful_Umetaladditionrates
                print "\n\n\n"

                #For chemistry calculations, it would be nice to have all data recorded in atom fraction format.
                # The core writer script provides values in mass fractions.
                # Thus, to record atom fraction data, we just need to copy the .bumat0 file if burnttime==0
                if burnttime==0:
                        inputfile.CopyBurntMaterials(day0=True) #notice the day0 parameter. this doesn't really copy the stuff to the new input. it actually just converts initial densities to atom densities for data collection.
                inputfile.CopyBurntMaterials()

                #Now record the inputfile object as a JSON-like thing. i.e. a pickle!
                with open("./inputfileslog/inputday{0}.dat".format(int(burnttime)),"w") as f:
                        pickle.dump(inputfile, f)

                #now copy burnt materials into the input file
                burnttime+=timeincrement #increment the burn timer
        else:
                raise Exception("Keff was read incorrectly. Probably should check serpent output files.")


endtime=time.asctime()
print "Job started at {0} \n and finished at {1}".format(starttime,endtime)